from collections.abc import AsyncIterator

from src.deps.llm.client import LlmCaller
from src.ingest.context import IngestContext
from src.ingest.events import ClassifyingSpine, IngestEvent
from src.ingest.llm_tasks.spine_classification_task import (
    SpineClassificationInput,
    SpineClassificationTask,
)


class ClassifySpineStep:
    """Run the spine-classification LLM task and write Book + Parts + Chapters
    onto the context."""

    name = "classify_spine"

    def __init__(self, llm_client: LlmCaller) -> None:
        self._llm_client = llm_client

    async def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]:
        assert ctx.parsed is not None and ctx.file_hash is not None
        yield ClassifyingSpine()
        output = await SpineClassificationTask.execute(
            self._llm_client,
            SpineClassificationInput(
                parsed=ctx.parsed,
                file_path=str(ctx.book_path),
                file_hash=ctx.file_hash,
            ),
        )
        ctx.book = output.book
        ctx.parts = output.parts
        ctx.chapters = output.chapters
