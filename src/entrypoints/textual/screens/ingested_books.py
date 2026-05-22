import logging
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Tree

from src.deps.postgres.tables import Chapter, Section

if TYPE_CHECKING:
    from src.entrypoints.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class IngestedBooksScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            yield DataTable(id="books", cursor_type="row")
            yield Tree("(select a book)", id="tree")
        yield Footer()

    async def on_mount(self) -> None:
        self.sub_title = "Ingested books"
        self._book_ids: list[str] = []
        table = self.query_one("#books", DataTable)
        table.add_columns("Title", "Author")
        await self._load_books()

    async def _load_books(self) -> None:
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        try:
            books = await app.deps.book_repo.list_books()
        except Exception as exc:
            logger.warning("could not load books: %s", exc)
            books = []

        table = self.query_one("#books", DataTable)
        if not books:
            table.add_row("(no ingested books)", "")
            return
        for book in books:
            self._book_ids.append(book.id)
            table.add_row(book.title, book.author or "")

    @on(DataTable.RowSelected)
    async def _row_selected(self, event: DataTable.RowSelected) -> None:
        if not self._book_ids:
            return
        book_id = self._book_ids[event.cursor_row]
        await self._populate_tree(book_id)

    async def _populate_tree(self, book_id: str) -> None:
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        tree = self.query_one("#tree", Tree)
        tree.clear()

        structure = await app.deps.book_repo.load_structure(book_id)
        if structure is None:
            tree.root.label = "(book not found)"
            return

        tree.root.label = structure.book.title
        tree.root.expand()

        if structure.parts:
            for part in structure.parts:
                part_node = tree.root.add(f"[bold]{part.title}[/bold]", expand=True)
                for ch in structure.chapters:
                    if ch.parent_part_id != part.id:
                        continue
                    self._add_chapter_node(
                        part_node, ch, structure.sections_by_chapter.get(ch.id, [])
                    )
            orphan_chapters = [c for c in structure.chapters if c.parent_part_id is None]
            if orphan_chapters:
                misc = tree.root.add("[dim](no part)[/dim]", expand=True)
                for ch in orphan_chapters:
                    self._add_chapter_node(misc, ch, structure.sections_by_chapter.get(ch.id, []))
        else:
            for ch in structure.chapters:
                self._add_chapter_node(tree.root, ch, structure.sections_by_chapter.get(ch.id, []))

    @staticmethod
    def _add_chapter_node(parent_node, chapter: Chapter, sections: list[Section]) -> None:
        node = parent_node.add(chapter.title, expand=False)
        for s in sections:
            node.add_leaf(s.title)
