import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.deps.postgres.table_rows import BookRow
from src.deps.postgres.table_rows.chapter_row import ChapterRow
from src.deps.postgres.table_rows import PartRow
from src.deps.postgres.table_rows import SectionRow
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Tree

if TYPE_CHECKING:
    from src.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class IngestedBooksScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        """Lay out a two-pane view: books DataTable on the left, structure Tree on the right.

        Intent: the table is the index ("which book?"), the tree is the drill-down
        ("what's inside?") — selecting a row repopulates the tree.
        """
        yield Header(show_clock=False)
        with Horizontal():
            yield DataTable(id="books", cursor_type="row")
            yield Tree("(select a book)", id="tree")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize the books table headers and trigger the initial DB load.

        Intent: do the IO once at mount time so the screen is fully populated by the
        time it becomes visible; reselects re-query but the initial paint doesn't wait.
        """
        self.sub_title = "Ingested books"
        self._book_ids: list[str] = []
        table = self.query_one("#books", DataTable)
        table.add_columns("Title", "Author")
        await self._load_books()

    async def _load_books(self) -> None:
        """Populate the left-hand DataTable with every Book in Postgres.

        Intent: keep this lookup independent of the tree so the user can browse books
        even if a particular book's children query fails — and stash row → book_id
        mapping for the click handler.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        try:
            async with app.deps.db.session() as session:
                result = await session.execute(
                    select(BookRow.id, BookRow.title, BookRow.author).order_by(BookRow.created_at)
                )
                rows = result.all()
        except Exception as exc:
            logger.warning("could not load books: %s", exc)
            rows = []

        table = self.query_one("#books", DataTable)
        if not rows:
            table.add_row("(no ingested books)", "")
            return
        for book_id, title, author in rows:
            self._book_ids.append(book_id)
            table.add_row(title, author or "")

    @on(DataTable.RowSelected)
    async def _row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle a book-row selection by repopulating the right-hand tree.

        Intent: every selection is a fresh load — simpler than caching tree state per
        book and the structure is small enough that re-querying is cheap.
        """
        if not self._book_ids:
            return
        book_id = self._book_ids[event.cursor_row]
        await self._populate_tree(book_id)

    async def _populate_tree(self, book_id: str) -> None:
        """Load the chosen book's full hierarchy and render it into the Tree widget.

        Intent: assemble the entire (parts → chapters → sections) outline in one
        session so the tree is rendered atomically — three queries inside one
        connection, then a single render pass with the result.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        tree = self.query_one("#tree", Tree)
        tree.clear()

        async with app.deps.db.session() as session:
            book = await session.get(BookRow, book_id)
            if book is None:
                tree.root.label = "(book not found)"
                return
            tree.root.label = f"{book.title}"
            tree.root.expand()

            parts = (
                (
                    await session.execute(
                        select(PartRow)
                        .where(PartRow.parent_book_id == book_id)
                        .order_by(PartRow.order_index)
                    )
                )
                .scalars()
                .all()
            )
            chapters = (
                (
                    await session.execute(
                        select(ChapterRow)
                        .where(ChapterRow.parent_book_id == book_id)
                        .order_by(ChapterRow.order_index)
                    )
                )
                .scalars()
                .all()
            )
            sections_by_chapter: dict[str, list[SectionRow]] = {}
            section_rows = (
                (
                    await session.execute(
                        select(SectionRow)
                        .where(SectionRow.parent_chapter_id.in_([c.id for c in chapters]))
                        .order_by(SectionRow.parent_chapter_id, SectionRow.order_index)
                    )
                )
                .scalars()
                .all()
                if chapters
                else []
            )
            for s in section_rows:
                sections_by_chapter.setdefault(s.parent_chapter_id, []).append(s)

        if parts:
            for part in parts:
                part_node = tree.root.add(f"[bold]{part.title}[/bold]", expand=True)
                for ch in chapters:
                    if ch.parent_part_id != part.id:
                        continue
                    self._add_chapter_node(part_node, ch, sections_by_chapter.get(ch.id, []))
            orphan_chapters = [c for c in chapters if c.parent_part_id is None]
            if orphan_chapters:
                misc = tree.root.add("[dim](no part)[/dim]", expand=True)
                for ch in orphan_chapters:
                    self._add_chapter_node(misc, ch, sections_by_chapter.get(ch.id, []))
        else:
            for ch in chapters:
                self._add_chapter_node(tree.root, ch, sections_by_chapter.get(ch.id, []))

    @staticmethod
    def _add_chapter_node(parent_node, chapter: ChapterRow, sections: list[SectionRow]) -> None:
        """Append a Chapter node (with its Section leaves) under the given tree parent.

        Intent: centralize how a chapter is rendered so the parted-book and unparted-book
        branches in `_populate_tree` don't duplicate this small bit of formatting.
        """
        node = parent_node.add(chapter.title, expand=False)
        for s in sections:
            node.add_leaf(s.title)
