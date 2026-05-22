from src.deps.epub_ingest import SpineItem
from src.deps.postgres.tables import Chapter, Section
from src.workflows.ingest.llm_tasks.sections_fallback_task import build_sections_from_offsets


class HeadingBasedStrategy:
    """Deterministic strategy: carve the chapter by its `<h3>` heading offsets.

    The cheapest path — when the publisher has marked sub-structure in the source,
    we trust it and don't call the LLM.
    """

    name = "heading_based"

    def can_handle(self, spine_item: SpineItem, chapter: Chapter) -> bool:
        return any(h.level == 3 for h in spine_item.headings)

    async def resolve(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        h3s = [h for h in spine_item.headings if h.level == 3]
        offsets = [(h.text, h.char_offset) for h in h3s]
        return build_sections_from_offsets(offsets, spine_item, chapter)
