import logging
from dataclasses import dataclass

from src.deps.claude_client import ClaudeClient
from src.deps.epub_ingest import EpubIngestReader
from src.deps.postgres.database import Database
from src.services.book_component_breakdown_service import BookComponentBreakdownService
from src.settings import Settings, get_settings
from src.textual.screens.file_picker import FilePickerScreen
from src.textual.screens.ingested_books import IngestedBooksScreen
from textual.app import App

logger = logging.getLogger(__name__)


@dataclass
class AppDeps:
    settings: Settings
    epub_reader: EpubIngestReader
    claude_client: ClaudeClient
    db: Database
    service: BookComponentBreakdownService


class ZizekMapApp(App):
    CSS = """
    Screen { layout: vertical; }
    DataTable { height: 1fr; }
    """

    BINDINGS = [
        ("f", "push_screen('file_picker')", "Pick file"),
        ("i", "push_screen('ingested')", "Browse ingested"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, deps: AppDeps) -> None:
        """Attach the pre-built AppDeps bundle so every screen can reach the shared services.

        Intent: keep dependency wiring centralized in `run()` — screens look up deps via
        `self.app.deps` and never construct clients/sessions on their own.
        """
        super().__init__()
        self.deps = deps

    def on_mount(self) -> None:
        """Install the named top-level screens and land the user on the file picker.

        Intent: the file picker is the default landing because the most common action
        is "pick an epub to ingest"; the ingested-books browser is one keystroke away.
        """
        self.install_screen(FilePickerScreen(), name="file_picker")
        self.install_screen(IngestedBooksScreen(), name="ingested")
        self.push_screen("file_picker")


def run() -> None:
    """Build the full dependency graph and start the Textual UI event loop.

    Intent: the single process entry point — `main.py` calls this and nothing else.
    All construction of clients, sessions, and services happens here so the rest of
    the codebase never sees a constructor call to a dep.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    settings = get_settings()
    epub_reader = EpubIngestReader()
    claude_client = ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    db = Database(database_url=settings.database_url)
    service = BookComponentBreakdownService(epub_reader=epub_reader, claude_client=claude_client)
    deps = AppDeps(
        settings=settings,
        epub_reader=epub_reader,
        claude_client=claude_client,
        db=db,
        service=service,
    )
    app = ZizekMapApp(deps=deps)
    app.run()
