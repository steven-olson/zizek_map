import logging

from src.deps.epub_ingest import HeadingMatch, ParsedEpub, SpineItem, TocNode
from src.deps.llm.llm_client import LlmClient
from src.deps.llm.prompts.sections_fallback import SectionsFallbackPrompt
from src.models.llm_responses import SectionsResponse
from src.models.text_components import Chapter, Section

logger = logging.getLogger(__name__)

_SECTION_FALLBACK_THRESHOLD = 4000


class ChapterSectioningError(RuntimeError):
    pass


class ChapterSectioningService:
    """Find the section boundaries inside a single chapter and emit Section objects.

    Owns steps 5 + 6 of ingestion for one chapter at a time. The workflow loops over
    chapters and calls `find_sections` per chapter, so the workflow controls iteration
    while this service controls strategy.

    Strategy order, most deterministic first:
      1. TOC sub-entries — if toc.ncx nests #fragment children under the chapter,
         resolve each fragment to a char offset via the spine item's id_offsets.
      2. h3 headings inside the chapter file.
      3. LLM fallback when the chapter is long enough to plausibly have sub-structure
         but neither of the above produced any boundaries.
      4. Single section spanning the whole chapter, for short chapters with no signal.
    """

    def __init__(self, llm_client: LlmClient) -> None:
        """Stash the LLM client used for the section-fallback call.

        Intent: dependency injection so the workflow controls lifecycle and tests
        can substitute a fake without monkey-patching.
        """
        self._llm_client = llm_client

    async def find_sections(self, parsed: ParsedEpub, chapter: Chapter) -> list[Section]:
        """Resolve the sections of `chapter` using the most deterministic available strategy.

        Intent: pick the best signal we have. The chapter's char range is the whole
        spine item (set by BookSkeletonService); we tile it with sections that share
        the same spine_file_path and never overlap.
        """
        spine_item = self._lookup_spine_item(parsed, chapter)

        toc_sections = self._sections_from_toc(parsed, spine_item, chapter)
        if toc_sections:
            logger.info(
                "chapter=%r sections=%d resolved from TOC fragments",
                chapter.title,
                len(toc_sections),
            )
            return toc_sections

        h3s = [h for h in spine_item.headings if h.level == 3]
        if h3s:
            logger.info(
                "chapter=%r sections=%d resolved from h3 headings",
                chapter.title,
                len(h3s),
            )
            return self._sections_from_headings(h3s, spine_item, chapter)

        if len(spine_item.plaintext) < _SECTION_FALLBACK_THRESHOLD:
            logger.info(
                "chapter=%r is short and has no sub-structure; emitting single section",
                chapter.title,
            )
            return [self._single_section(spine_item, chapter)]

        logger.info(
            "chapter=%r has no TOC fragments and no h3s; falling back to LLM section detection",
            chapter.title,
        )
        return await self._sections_from_llm(spine_item, chapter)

    @staticmethod
    def _lookup_spine_item(parsed: ParsedEpub, chapter: Chapter) -> SpineItem:
        """Find the spine item that backs this chapter, raising if absent.

        Intent: chapters carry `spine_file_path` rather than a direct SpineItem ref to
        keep the domain model serializable; this is the one place that joins them back.
        """
        spine_item = next(
            (item for item in parsed.spine if item.file_path == chapter.spine_file_path),
            None,
        )
        if spine_item is None:
            raise ChapterSectioningError(
                f"chapter references unknown spine file {chapter.spine_file_path!r}"
            )
        return spine_item

    def _sections_from_toc(
        self, parsed: ParsedEpub, spine_item: SpineItem, chapter: Chapter
    ) -> list[Section] | None:
        """If the TOC nests section-level entries under this chapter, emit them.

        Intent: a chapter-level TOC node may have children that all point at the same
        spine file with `#fragment` identifiers — those fragments are section markers
        (this is the pattern Calibre uses for *Less Than Nothing*). We resolve each
        fragment to a char offset via the spine item's id_offsets and tile the chapter
        with one Section per fragment. Returns None if the TOC doesn't carry this info,
        so the caller can fall through to heading- or LLM-based strategies.
        """
        node = self._find_toc_node_for_file(parsed.toc, chapter.spine_file_path)
        if node is None or not node.children:
            return None

        offsets: list[tuple[str, int]] = []
        for child in node.children:
            if child.file_path != chapter.spine_file_path or child.fragment is None:
                continue
            offset = spine_item.id_offsets.get(child.fragment)
            if offset is None:
                logger.warning(
                    "TOC fragment %r not found in spine file %r for chapter=%r; skipping",
                    child.fragment,
                    chapter.spine_file_path,
                    chapter.title,
                )
                continue
            offsets.append((child.label.strip(), offset))

        if not offsets:
            return None

        offsets.sort(key=lambda t: t[1])
        return self._sections_from_offsets(offsets, spine_item, chapter)

    @classmethod
    def _find_toc_node_for_file(cls, toc: list[TocNode], file_path: str) -> TocNode | None:
        """Locate the TOC node that represents the whole of a given spine file.

        Intent: prefer a node that points at `file_path` with no fragment (the chapter-
        level entry) so its children can be interpreted as sub-sections. Falls back to
        any node pointing at the file if no fragment-less match exists.
        """
        fallback: TocNode | None = None
        for node in toc:
            if node.file_path == file_path:
                if node.fragment is None:
                    return node
                if fallback is None:
                    fallback = node
            descendant = cls._find_toc_node_for_file(node.children, file_path)
            if descendant is not None and descendant.fragment is None:
                return descendant
            if descendant is not None and fallback is None:
                fallback = descendant
        return fallback

    def _sections_from_headings(
        self,
        h3s: list[HeadingMatch],
        spine_item: SpineItem,
        chapter: Chapter,
    ) -> list[Section]:
        """Carve a chapter into sections delimited by its h3 char offsets.

        Intent: the fully deterministic path — each h3 begins a section that runs until
        the next h3 (or the chapter's end), so no LLM call is needed when the publisher
        marked sub-structure in the source.
        """
        offsets = [(h.text, h.char_offset) for h in h3s]
        return self._sections_from_offsets(offsets, spine_item, chapter)

    async def _sections_from_llm(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        """Ask the LLM to find section breaks in a chapter that has no structural markers.

        Intent: a fallback for the messy case — the prompt returns titles + short verbatim
        excerpts; we string-match each excerpt to recover deterministic char offsets and
        emit one Section per matched excerpt. If nothing matches, the whole chapter
        becomes a single section.
        """
        response = await self._llm_client.call_structured(
            system=SectionsFallbackPrompt.SYSTEM,
            user=SectionsFallbackPrompt.build_user(spine_item, chapter),
            response_model=SectionsResponse,
        )

        offsets = self._locate_excerpt_offsets(response, spine_item, chapter)
        if not offsets:
            return [self._single_section(spine_item, chapter)]
        return self._sections_from_offsets(offsets, spine_item, chapter)

    @staticmethod
    def _locate_excerpt_offsets(
        response: SectionsResponse, spine_item: SpineItem, chapter: Chapter
    ) -> list[tuple[str, int]]:
        """Resolve each LLM-suggested section to a `(title, char_offset)` via string-match.

        Excerpts that don't appear verbatim in the plaintext are dropped (with a warning).
        Result is sorted by offset so the caller can carve consecutive ranges.
        """
        located: list[tuple[str, int]] = []
        for sb in response.sections:
            offset = spine_item.plaintext.find(sb.first_sentence_excerpt)
            if offset == -1:
                logger.warning(
                    "could not locate section %r via excerpt %r in chapter=%r; skipping",
                    sb.title,
                    sb.first_sentence_excerpt,
                    chapter.title,
                )
                continue
            located.append((sb.title, offset))
        located.sort(key=lambda t: t[1])
        return located

    @staticmethod
    def _sections_from_offsets(
        offsets: list[tuple[str, int]], spine_item: SpineItem, chapter: Chapter
    ) -> list[Section]:
        """Turn a sorted list of `(title, char_start)` tuples into Section objects.

        Each section's `char_end` is the next section's start, or the chapter end for
        the last entry — so the sections tile the chapter without gaps or overlaps.
        """
        sections: list[Section] = []
        for idx, (title, char_start) in enumerate(offsets):
            char_end = offsets[idx + 1][1] if idx + 1 < len(offsets) else chapter.char_end
            sections.append(
                Section(
                    title=title,
                    order_index=idx,
                    spine_file_path=spine_item.file_path,
                    char_start=char_start,
                    char_end=char_end,
                    parent_chapter_id=chapter.id,
                )
            )
        return sections

    @staticmethod
    def _single_section(spine_item: SpineItem, chapter: Chapter) -> Section:
        """Build a single Section that spans the whole chapter.

        Intent: shared by both "short chapter, no markers" and "LLM returned no usable
        excerpts" paths so the fallback shape is defined in exactly one place.
        """
        return Section(
            title=chapter.title,
            order_index=0,
            spine_file_path=spine_item.file_path,
            char_start=chapter.char_start,
            char_end=chapter.char_end,
            parent_chapter_id=chapter.id,
        )
