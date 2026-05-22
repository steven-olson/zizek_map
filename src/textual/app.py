import logging
from dataclasses import dataclass

from src.deps.epub_ingest import EpubIngestReader
from src.deps.llm.llm_client import LlmClient
from src.deps.postgres.database import Database
from src.services.book_skeleton_service import BookSkeletonService
from src.services.chapter_sectioning_service import ChapterSectioningService
from src.settings import Settings, get_settings
from src.textual.screens.file_picker import FilePickerScreen
from src.textual.screens.ingested_books import IngestedBooksScreen
from src.workflows.book_ingest_workflow import BookIngestWorkflow
from textual.app import App

logger = logging.getLogger(__name__)


@dataclass
class AppDeps:
    settings: Settings
    epub_reader: EpubIngestReader
    llm_client: LlmClient
    db: Database
    ingest_workflow: BookIngestWorkflow


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
    llm_client = LlmClient(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
    )
    db = Database(database_url=settings.database_url)
    skeleton_service = BookSkeletonService(llm_client=llm_client)
    sectioning_service = ChapterSectioningService(llm_client=llm_client)
    ingest_workflow = BookIngestWorkflow(
        epub_reader=epub_reader,
        skeleton_service=skeleton_service,
        sectioning_service=sectioning_service,
        db=db,
    )
    deps = AppDeps(
        settings=settings,
        epub_reader=epub_reader,
        llm_client=llm_client,
        db=db,
        ingest_workflow=ingest_workflow,
    )
    app = ZizekMapApp(deps=deps)
    app.run()
