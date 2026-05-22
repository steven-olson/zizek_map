import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header

from src.entrypoints.textual.screens.ingest import IngestScreen

if TYPE_CHECKING:
    from src.entrypoints.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class FilePickerScreen(Screen):
    BINDINGS = [("enter", "ingest", "Ingest selected")]

    def compose(self) -> ComposeResult:
        """Lay out a header + DataTable of epubs + footer."""
        yield Header(show_clock=False)
        yield DataTable(id="files", cursor_type="row")
        yield Footer()

    async def on_mount(self) -> None:
        """Populate the table by listing the books dir and cross-checking the repo."""
        self.sub_title = "Pick an EPUB to ingest"
        table = self.query_one("#files", DataTable)
        table.add_columns("File", "Size (KB)", "Status")

        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        books_dir = app.deps.settings.app.books_dir
        ingested = await self._load_ingested_paths()

        epubs = app.deps.epub_reader.list_available_epubs(books_dir)
        if not epubs:
            table.add_row(f"(no .epub files in {books_dir})", "", "")
            self._epub_paths: list[Path] = []
            return

        self._epub_paths = epubs
        for epub_path in epubs:
            size_kb = f"{epub_path.stat().st_size / 1024:,.0f}"
            status = "ingested" if str(epub_path) in ingested else "not ingested"
            table.add_row(epub_path.name, size_kb, status)

    async def _load_ingested_paths(self) -> set[str]:
        """Return file_paths already in the books table, or an empty set on DB error.

        Degrades gracefully so the picker still works on a fresh dev setup before
        the first migration has been run.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        try:
            return await app.deps.book_repo.list_ingested_paths()
        except Exception as exc:
            logger.warning("could not load ingested books: %s", exc)
            return set()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if not self._epub_paths:
            return
        path = self._epub_paths[event.cursor_row]
        logger.info("user selected %s", path)
        self.app.push_screen(IngestScreen(epub_path=path))

    def action_ingest(self) -> None:
        table = self.query_one("#files", DataTable)
        if not self._epub_paths:
            return
        path = self._epub_paths[table.cursor_row]
        self.app.push_screen(IngestScreen(epub_path=path))
