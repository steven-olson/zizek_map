import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, RichLog

from src.ingest import (
    ChapterCompleted,
    ChapterStarted,
    ClassifyingSpine,
    Done,
    Failed,
    HashingFile,
    IngestEvent,
    ParsingEpub,
    Persisting,
    ReingestingExisting,
    SkippedAlreadyIngested,
)
from src.ingest.composition import build_ingest_pipeline

if TYPE_CHECKING:
    from src.entrypoints.textual.app import ZizekMapApp

logger = logging.getLogger(__name__)


class IngestScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, epub_path: Path) -> None:
        """Bind this screen to the specific epub it should ingest."""
        super().__init__()
        self._epub_path = epub_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", markup=True, wrap=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        """Print the banner line and kick off the pipeline in a worker."""
        self.sub_title = f"Ingesting {self._epub_path.name}"
        log = self.query_one("#log", RichLog)
        log.write(f"[bold]Ingesting:[/bold] {self._epub_path}")
        self.run_worker(self._run_pipeline(), exclusive=True, name="ingest")

    async def _run_pipeline(self) -> None:
        app: "ZizekMapApp" = self.app  # type: ignore[assignment]
        log = self.query_one("#log", RichLog)
        pipeline = build_ingest_pipeline(app.deps)
        try:
            async for event in pipeline.run(self._epub_path):
                log.write(self._format_event(event))
        except Exception as exc:
            log.write(f"[red bold]pipeline crashed:[/red bold] {type(exc).__name__}: {exc}")

    @staticmethod
    def _format_event(event: IngestEvent) -> str:
        """Render an IngestEvent into the rich-markup line that appears in the log."""
        match event:
            case ParsingEpub():
                return "[cyan]Parsing EPUB...[/cyan]"
            case HashingFile():
                return "[cyan]Hashing file...[/cyan]"
            case ReingestingExisting(book_id=bid):
                return f"[yellow]Replacing existing ingest:[/yellow] old book_id={bid}"
            case SkippedAlreadyIngested(book_id=bid):
                return f"[green bold]Skipped — already ingested[/green bold] book_id={bid}"
            case ClassifyingSpine():
                return "[cyan]Classifying spine items (LLM call)...[/cyan]"
            case ChapterStarted(chapter_title=title, index=i, total=n):
                return f"[cyan]Resolving sections {i + 1}/{n}:[/cyan] {title}"
            case ChapterCompleted(chapter_title=title, section_count=n):
                return f"[dim]  done:[/dim] {title} ({n} sections)"
            case Persisting(counts=counts):
                return f"[cyan]Persisting to Postgres:[/cyan] {counts}"
            case Done(book_id=book_id, counts=counts):
                return f"[green bold]Done.[/green bold] book_id={book_id} counts={counts}"
            case Failed(error=error):
                return f"[red bold]Failed:[/red bold] {error}"
