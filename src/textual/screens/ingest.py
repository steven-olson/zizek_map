import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.workflows.book_component_breakdown_workflow import (
    BookComponentBreakdownWorkflow,
    ClassifyingSpine,
    Done,
    Failed,
    IngestEvent,
    ParsingEpub,
    Persisting,
    ResolvingSections,
)
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

        Intent: the RichLog is the live transcript of `IngestEvent`s — it's the only
        widget on the screen because progress reporting is the whole job here.
        """
        yield Header(show_clock=False)
        yield RichLog(id="log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        """Print the banner line and kick off the workflow worker in the background.

        Intent: `run_worker` schedules the async generator on Textual's event loop so
        the UI stays responsive and the log streams in as events arrive.
        """
        self.sub_title = f"Ingesting {self._epub_path.name}"
        log = self.query_one("#log", RichLog)
        log.write(f"[bold]Ingesting:[/bold] {self._epub_path}")
        self.run_worker(self._run_workflow(), exclusive=True, name="ingest")

    async def _run_workflow(self) -> None:
        """Build a workflow for this epub and pump its event stream into the RichLog.

        Intent: this is the bridge between the headless workflow and the on-screen
        widget — any exception that escapes the workflow gets surfaced as a final
        crash line so the user sees a definite outcome.
        """
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        log = self.query_one("#log", RichLog)
        workflow = BookComponentBreakdownWorkflow(
            book_path=self._epub_path,
            service=app.deps.service,
            epub_reader=app.deps.epub_reader,
            db=app.deps.db,
        )
        try:
            async for event in workflow.run():
                log.write(self._format_event(event))
        except Exception as exc:
            log.write(f"[red bold]workflow crashed:[/red bold] {type(exc).__name__}: {exc}")

    @staticmethod
    def _format_event(event: IngestEvent) -> str:
        """Render an `IngestEvent` into the rich-markup line that appears in the log.

        Intent: one place to control how progress looks to the user — color, prefix,
        and any numeric framing (e.g. "3/11") all live here, so changing the visual
        treatment never touches the workflow.
        """
        match event:
            case ParsingEpub():
                return "[cyan]Parsing EPUB...[/cyan]"
            case ClassifyingSpine():
                return "[cyan]Classifying spine items (LLM call)...[/cyan]"
            case ResolvingSections(chapter_title=title, index=i, total=n):
                return f"[cyan]Resolving sections {i + 1}/{n}:[/cyan] {title}"
            case Persisting(counts=counts):
                return f"[cyan]Persisting to Postgres:[/cyan] {counts}"
            case Done(book_id=book_id, counts=counts):
                return f"[green bold]Done.[/green bold] book_id={book_id} counts={counts}"
            case Failed(error=error):
                return f"[red bold]Failed:[/red bold] {error}"
