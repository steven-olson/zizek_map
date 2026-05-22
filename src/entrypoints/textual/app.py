import logging

from textual.app import App

from src.entrypoints.textual.screens.file_picker import FilePickerScreen
from src.entrypoints.textual.screens.ingested_books import IngestedBooksScreen
from src.workflows.ingest import AppDeps, build_deps

logger = logging.getLogger(__name__)


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
        """Attach the pre-built AppDeps bundle so every screen can reach shared services."""
        super().__init__()
        self.deps = deps

    def on_mount(self) -> None:
        """Install the named top-level screens and land the user on the file picker."""
        self.install_screen(FilePickerScreen(), name="file_picker")
        self.install_screen(IngestedBooksScreen(), name="ingested")
        self.push_screen("file_picker")


def run() -> None:
    """Build the dependency graph and start the Textual UI event loop.

    Intent: thin wrapper — composition.build_deps() owns dependency wiring; this
    module only knows how to render screens given the wired AppDeps.
    """
    deps = build_deps()
    app = ZizekMapApp(deps=deps)
    app.run()
