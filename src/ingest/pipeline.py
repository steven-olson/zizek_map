import logging
from collections.abc import AsyncIterator
from pathlib import Path

from src.ingest.context import IngestContext
from src.ingest.events import Done, Failed, IngestEvent
from src.ingest.steps import Step

logger = logging.getLogger(__name__)


class IngestPipeline:
    """A small linear runner: invoke each step's `execute(ctx)` and stream the events
    it yields. If `ctx.skipped` is set by a step, subsequent steps are not run.

    Intent: the pipeline is a *declaration* — give it the ordered list of steps and
    it walks them — so adding a stage means adding a Step class, not editing the
    runner. Exceptions from any step are wrapped in a final `Failed` event before
    being re-raised so the UI always sees a definite outcome.
    """

    def __init__(self, steps: list[Step]) -> None:
        self._steps = steps

    async def run(self, book_path: Path) -> AsyncIterator[IngestEvent]:
        logger.info("pipeline start book_path=%s", book_path)
        ctx = IngestContext(book_path=book_path)
        try:
            for step in self._steps:
                if ctx.skipped:
                    break
                logger.info("pipeline step=%s start", step.name)
                async for event in step.execute(ctx):
                    yield event
                logger.info("pipeline step=%s done", step.name)
            if not ctx.skipped and ctx.book is not None:
                logger.info("pipeline complete book_id=%s counts=%s", ctx.book.id, ctx.counts())
                yield Done(book_id=ctx.book.id, counts=ctx.counts())
        except Exception as exc:
            logger.exception("pipeline failed book_path=%s", book_path)
            yield Failed(error=f"{type(exc).__name__}: {exc}")
            raise
