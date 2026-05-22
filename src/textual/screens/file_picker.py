import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.deps.postgres.table_rows import BookRow
from src.textual.screens.ingest import IngestScreen
from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header

if TYPE_CHECKING:
    from src.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class FilePickerScreen(Screen):
    BINDINGS = [("enter", "ingest", "Ingest selected")]

    def compose(self) -> ComposeResult:
        """Lay out the screen: header + a single-column DataTable of epubs + footer.

        Intent: keep this screen visually trivial — one table, row-cursor selection,
        Enter to ingest. The actual data population happens in `on_mount`.
        """
        yield Header(show_clock=False)
        yield DataTable(id="files", cursor_type="row")
        yield Footer()

    async def on_mount(self) -> None:
        """Populate the table by listing the books dir and cross-checking against Postgres.

        Intent: give the user instant signal about which files are already in the DB
        so they don't accidentally re-ingest. Failures in the DB lookup fall back to
        an empty 'ingested' set rather than blocking the picker.
        """
        self.sub_title = "Pick an EPUB to ingest"
        table = self.query_one("#files", DataTable)
        table.add_columns("File", "Size (KB)", "Status")

        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        books_dir = app.deps.settings.books_dir
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
        """Return the set of `file_path` values already present in the books table.

        Intent: cheap lookup used purely for the status badge — degrades gracefully
        if Postgres is unreachable so the picker still works for fresh dev setups.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        try:
            async with app.deps.db.session() as session:
                result = await session.execute(select(BookRow.file_path))
                return set(result.scalars().all())
        except Exception as exc:
            logger.warning("could not load ingested books: %s", exc)
            return set()

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        """Push the ingest screen when the user clicks/Enters a file row.

        Intent: same path as the keyboard `action_ingest` — both inputs converge on
        creating a fresh IngestScreen so the workflow runs once per user action.
        """
        if not self._epub_paths:
            return
        path = self._epub_paths[event.cursor_row]
        logger.info("user selected %s", path)
        self.app.push_screen(IngestScreen(epub_path=path))

    def action_ingest(self) -> None:
        """Keyboard handler that triggers ingest on whichever file the cursor is on.

        Intent: complement the click-to-select flow with a pure-keyboard path so
        users can drive the UI without leaving the home row.
        """
        table = self.query_one("#files", DataTable)
        if not self._epub_paths:
            return
        path = self._epub_paths[table.cursor_row]
        self.app.push_screen(IngestScreen(epub_path=path))
