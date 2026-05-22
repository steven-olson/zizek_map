import logging
from typing import Protocol

from src.deps.epub_ingest import ParsedEpub, SpineItem
from src.deps.postgres.tables import Chapter, Section

logger = logging.getLogger(__name__)


class SectionResolutionError(RuntimeError):
    pass


class SectionStrategy(Protocol):
    """One way of carving a Chapter into Sections.

    Implementations: HeadingBasedStrategy (deterministic, when h3s exist),
    SingleSectionStrategy (the whole chapter is one section, used when there are no
    h3s and the chapter is short), and LlmFallbackStrategy (ask the LLM to find
    topic breaks, used when there are no h3s and the chapter is long).
    """

    name: str

    def can_handle(self, spine_item: SpineItem, chapter: Chapter) -> bool:
        """Return True iff this strategy is appropriate for the given chapter."""
        ...

    async def resolve(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        """Produce the chapter's Sections."""
        ...


class SectionResolver:
    """Picks the first applicable SectionStrategy for each chapter.

    Intent: keep the strategies independent and self-describing — the resolver doesn't
    know what each strategy does, only how to ask them and how to find the right
    spine item for a chapter.
    """

    def __init__(self, strategies: list[SectionStrategy], file_hash: str) -> None:
        self._strategies = strategies
        self._file_hash = file_hash

    async def resolve_for_chapter(self, parsed: ParsedEpub, chapter: Chapter) -> list[Section]:
        """Locate the chapter's spine item and dispatch to the first matching strategy."""
        spine_item = next(
            (item for item in parsed.spine if item.file_path == chapter.spine_file_path),
            None,
        )
        if spine_item is None:
            raise SectionResolutionError(
                f"chapter references unknown spine file {chapter.spine_file_path!r}"
            )
        for strategy in self._strategies:
            if strategy.can_handle(spine_item, chapter):
                logger.info("resolving chapter=%r via strategy=%s", chapter.title, strategy.name)
                return await strategy.resolve(spine_item, chapter)
        raise SectionResolutionError(f"no section strategy could handle chapter {chapter.title!r}")

    @property
    def file_hash(self) -> str:
        return self._file_hash
