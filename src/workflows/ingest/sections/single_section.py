from src.deps.epub_ingest import SpineItem
from src.deps.postgres.tables import Chapter, Section


class SingleSectionStrategy:
    """Strategy of last resort: the whole chapter is one section.

    Picked when there are no h3 headings and the chapter is too short to plausibly
    have sub-structure (under a configurable plaintext-character threshold). Also
    used by LlmFallbackStrategy as its own fallback when the LLM finds nothing.
    """

    def __init__(self, plaintext_threshold: int) -> None:
        self._plaintext_threshold = plaintext_threshold

    name = "single_section"

    def can_handle(self, spine_item: SpineItem, chapter: Chapter) -> bool:
        h3s = [h for h in spine_item.headings if h.level == 3]
        return not h3s and len(spine_item.plaintext) < self._plaintext_threshold

    async def resolve(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        return [self.build(spine_item, chapter)]

    @staticmethod
    def build(spine_item: SpineItem, chapter: Chapter) -> Section:
        """Return one Section spanning the whole chapter — shared shape used by both
        this strategy and the LLM fallback's no-excerpts-matched branch."""
        return Section(
            title=chapter.title,
            order_index=0,
            spine_file_path=spine_item.file_path,
            char_start=chapter.char_start,
            char_end=chapter.char_end,
            parent_chapter_id=chapter.id,
        )
