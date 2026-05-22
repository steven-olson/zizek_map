import asyncio
import logging
from collections.abc import AsyncIterator

from src.deps.concurrency import BoundedConcurrentRunner
from src.deps.llm.client import LlmCaller
from src.deps.postgres.tables import Chapter, Section
from src.ingest.context import IngestContext
from src.ingest.events import ChapterCompleted, ChapterStarted, IngestEvent
from src.ingest.sections import (
    HeadingBasedStrategy,
    LlmFallbackStrategy,
    SectionResolver,
    SingleSectionStrategy,
)

logger = logging.getLogger(__name__)


class ResolveSectionsStep:
    """Resolve sections for every chapter in parallel, bounded by the LLM concurrency limit.

    Builds its SectionResolver per pipeline run because the LLM-fallback strategy's
    cache key depends on the file hash, which is only known after ParseEpubStep has
    run. Per-chapter `ChapterStarted` / `ChapterCompleted` events are emitted as
    workers pick up and finish chapters, so the UI sees real-time progress even
    though the resolutions overlap. Output order matches input chapter order.
    """

    name = "resolve_sections"

    def __init__(
        self,
        llm_client: LlmCaller,
        runner: BoundedConcurrentRunner,
        plaintext_threshold: int,
    ) -> None:
        self._llm_client = llm_client
        self._runner = runner
        self._plaintext_threshold = plaintext_threshold

    async def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]:
        assert ctx.parsed is not None and ctx.file_hash is not None
        parsed = ctx.parsed
        resolver = self._build_resolver(ctx.file_hash)
        chapters = ctx.chapters
        total = len(chapters)
        queue: asyncio.Queue[IngestEvent | None] = asyncio.Queue()

        async def worker(index: int, chapter: Chapter) -> list[Section]:
            await queue.put(ChapterStarted(chapter_title=chapter.title, index=index, total=total))
            sections = await resolver.resolve_for_chapter(parsed, chapter)
            await queue.put(
                ChapterCompleted(chapter_title=chapter.title, section_count=len(sections))
            )
            return sections

        async def runner_task() -> list[list[Section]]:
            try:
                return await self._runner.gather([worker(i, ch) for i, ch in enumerate(chapters)])
            finally:
                await queue.put(None)

        task = asyncio.create_task(runner_task())
        while True:
            evt = await queue.get()
            if evt is None:
                break
            yield evt
        sections_per_chapter = await task
        for chapter_sections in sections_per_chapter:
            ctx.sections.extend(chapter_sections)
        logger.info("resolved sections total=%d across chapters=%d", len(ctx.sections), total)

    def _build_resolver(self, file_hash: str) -> SectionResolver:
        return SectionResolver(
            strategies=[
                HeadingBasedStrategy(),
                SingleSectionStrategy(plaintext_threshold=self._plaintext_threshold),
                LlmFallbackStrategy(llm_client=self._llm_client, file_hash=file_hash),
            ],
            file_hash=file_hash,
        )
