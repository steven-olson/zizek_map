import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog

if TYPE_CHECKING:
    from src.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class IngestScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, epub_path: Path) -> None:
        """Bind this screen to the specific epub it should ingest.

        Intent: a fresh IngestScreen is pushed per ingest action, so the path is
        captured at construction time and never mutates during the workflow run.
        """
        super().__init__()
        self._epub_path = epub_path

    def compose(self) -> ComposeResult:
        """Lay out a header + a RichLog (scrollable, markup-aware) + footer.

        Intent: the RichLog is where the start banner, final outcome, and any failure
        message land — there's no live event stream anymore, just a small summary.
        """
        yield Header(show_clock=False)
        yield RichLog(id="log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        """Print the banner line and kick off the workflow worker in the background.

        Intent: `run_worker` schedules the async call on Textual's event loop so the
        UI stays responsive while the ingest is running.
        """
        self.sub_title = f"Ingesting {self._epub_path.name}"
        log = self.query_one("#log", RichLog)
        log.write(f"[bold]Ingesting:[/bold] {self._epub_path}")
        log.write("[cyan]Running ingest pipeline... this may take a minute.[/cyan]")
        self.run_worker(self._run_workflow(), exclusive=True, name="ingest")

    async def _run_workflow(self) -> None:
        """Await the workflow and render its single-shot result (or any crash).

        Intent: this is the bridge between the headless workflow and the on-screen
        widget — success prints a count summary, failure prints a red crash line.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        log = self.query_one("#log", RichLog)
        try:
            result = await app.deps.ingest_workflow.run(self._epub_path)
        except Exception as exc:
            log.write(f"[red bold]workflow crashed:[/red bold] {type(exc).__name__}: {exc}")
            return
        counts = {
            "parts": len(result.parts),
            "chapters": len(result.chapters),
            "sections": len(result.sections),
        }
        log.write(f"[green bold]Done.[/green bold] book_id={result.book.id} counts={counts}")
