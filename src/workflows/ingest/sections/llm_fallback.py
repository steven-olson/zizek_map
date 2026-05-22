import logging

from src.deps.epub_ingest import SpineItem
from src.deps.llm.client import LlmCaller
from src.deps.postgres.tables import Chapter, Section
from src.workflows.ingest.llm_tasks.sections_fallback_task import (
    SectionsFallbackInput,
    SectionsFallbackTask,
    build_sections_from_offsets,
)
from src.workflows.ingest.sections.single_section import SingleSectionStrategy

logger = logging.getLogger(__name__)


class LlmFallbackStrategy:
    """Catch-all strategy: ask an LLM where the topic breaks are.

    Selected when there are no `<h3>` headings AND the chapter is long enough that
    sub-structure is plausible. If the model returns no usable excerpts, fall back to
    a single section spanning the whole chapter so downstream callers always get at
    least one row per chapter.
    """

    name = "llm_fallback"

    def __init__(self, llm_client: LlmCaller, file_hash: str) -> None:
        self._llm_client = llm_client
        self._file_hash = file_hash

    def can_handle(self, spine_item: SpineItem, chapter: Chapter) -> bool:
        return True  # last-resort strategy — always applicable

    async def resolve(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        offsets = await SectionsFallbackTask.execute(
            self._llm_client,
            SectionsFallbackInput(
                spine_item=spine_item, chapter=chapter, file_hash=self._file_hash
            ),
        )
        if not offsets:
            logger.info(
                "chapter=%r had no usable section excerpts; emitting single section",
                chapter.title,
            )
            return [SingleSectionStrategy.build(spine_item, chapter)]
        return build_sections_from_offsets(offsets, spine_item, chapter)
