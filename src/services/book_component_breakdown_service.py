import logging

from src.deps.epub_ingest import EpubIngestReader, HeadingMatch, ParsedEpub, SpineItem
from src.deps.llm_client import LlmClient
from src.deps.prompts.sections_fallback import SectionsFallbackPrompt
from src.deps.prompts.spine_classification import SpineClassificationPrompt
from src.models.llm_responses import SectionsResponse
from src.models.text_components import Book, Chapter, Part, Section

logger = logging.getLogger(__name__)

_SECTION_FALLBACK_THRESHOLD = 4000


class BookComponentBreakdownError(RuntimeError):
    pass


class BookComponentBreakdownService:
    """Business logic: given a parsed EPUB, classify its spine into Book + Parts +
    Chapters, then resolve Sections per chapter. Does not persist."""

    def __init__(self, epub_reader: EpubIngestReader, llm_client: LlmClient) -> None:
        """Hold the two deps this service composes: epub parsing and the LLM client.

        Intent: dependency injection so the workflow controls lifecycle and tests can
        swap either side with fakes without monkey-patching.
        """
        self._epub_reader = epub_reader
        self._llm_client = llm_client

    async def classify_book(
        self, parsed: ParsedEpub, file_path: str
    ) -> tuple[Book, list[Part], list[Chapter]]:
        """Decide which spine items are real chapters/parts and build domain objects for them.

        Intent: one LLM round-trip — the spine-classification prompt produces a role per
        spine item, then we synthesize Part / Chapter objects in deterministic spine order.
        """
        logger.info("classifying spine items=%d for book=%r", len(parsed.spine), parsed.title)

        response = await self._llm_client.call_structured(
            system=SpineClassificationPrompt.SYSTEM,
            user=SpineClassificationPrompt.build_user(parsed),
            response_model=SpineClassificationPrompt.RESPONSE_MODEL,
        )
        classifications_by_path = {item.file_path: item for item in response.items}

        book = Book(title=parsed.title, author=parsed.author, file_path=file_path)
        parts, part_id_by_file_path = self._build_parts(parsed, classifications_by_path, book.id)
        chapters = self._build_chapters(
            parsed, classifications_by_path, book.id, part_id_by_file_path
        )

        logger.info(
            "classification produced book=%s parts=%d chapters=%d",
            book.id,
            len(parts),
            len(chapters),
        )
        return book, parts, chapters

    async def resolve_sections_for_chapter(
        self, parsed: ParsedEpub, chapter: Chapter
    ) -> list[Section]:
        """Resolve sections within a single chapter, picking the strategy per chapter.

        Intent: the workflow calls this in a loop so it can yield a ResolvingSections
        event with the chapter's title each iteration — exposing the per-chapter
        granularity needed for live progress reporting.
        """
        spine_item = next(
            (item for item in parsed.spine if item.file_path == chapter.spine_file_path),
            None,
        )
        if spine_item is None:
            raise BookComponentBreakdownError(
                f"chapter references unknown spine file {chapter.spine_file_path!r}"
            )

        h3s = [h for h in spine_item.headings if h.level == 3]
        if h3s:
            return self._sections_from_headings(h3s, spine_item, chapter)
        if len(spine_item.plaintext) < _SECTION_FALLBACK_THRESHOLD:
            logger.info(
                "chapter=%r is short and has no h3s; emitting single section",
                chapter.title,
            )
            return [self._single_section(spine_item, chapter)]
        logger.info("chapter=%r has no h3s; falling back to LLM section detection", chapter.title)
        return await self._sections_from_llm(spine_item, chapter)

    def _build_parts(
        self,
        parsed: ParsedEpub,
        classifications_by_path: dict,
        book_id: str,
    ) -> tuple[list[Part], dict[str, str]]:
        """Synthesize one Part per `part_divider` spine item, in spine order.

        Returns the list of parts and a map from each part's spine file_path to its
        generated id, so chapters can resolve their `parent_part_id` by file_path.
        """
        parts: list[Part] = []
        part_id_by_file_path: dict[str, str] = {}
        for spine_item in parsed.spine:
            cls = classifications_by_path.get(spine_item.file_path)
            if cls is None or cls.role != "part_divider":
                continue
            part = Part(
                title=(cls.clean_title or spine_item.file_path).strip(),
                order_index=len(parts),
                parent_book_id=book_id,
            )
            parts.append(part)
            part_id_by_file_path[spine_item.file_path] = part.id
        return parts, part_id_by_file_path

    def _build_chapters(
        self,
        parsed: ParsedEpub,
        classifications_by_path: dict,
        book_id: str,
        part_id_by_file_path: dict[str, str],
    ) -> list[Chapter]:
        """Synthesize one Chapter per `chapter` spine item, linked to its parent Part if any.

        Intent: char range is the full spine item (0..len); section boundaries within
        that range are resolved in a later step.
        """
        chapters: list[Chapter] = []
        for spine_item in parsed.spine:
            cls = classifications_by_path.get(spine_item.file_path)
            if cls is None or cls.role != "chapter":
                continue
            parent_part_id = (
                part_id_by_file_path.get(cls.parent_part_file_path)
                if cls.parent_part_file_path
                else None
            )
            chapters.append(
                Chapter(
                    title=(cls.clean_title or spine_item.file_path).strip(),
                    order_index=len(chapters),
                    spine_file_path=spine_item.file_path,
                    char_start=0,
                    char_end=len(spine_item.plaintext),
                    parent_book_id=book_id,
                    parent_part_id=parent_part_id,
                )
            )
        return chapters

    def _sections_from_headings(
        self,
        h3s: list[HeadingMatch],
        spine_item: SpineItem,
        chapter: Chapter,
    ) -> list[Section]:
        """Carve a chapter into sections delimited by its h3 char offsets.

        Intent: the fully deterministic path — each h3 begins a section that runs
        until the next h3 (or the chapter's end), so no LLM call is needed when the
        publisher has marked sub-structure in the source.
        """
        offsets = [(h.text, h.char_offset) for h in h3s]
        return self._sections_from_offsets(offsets, spine_item, chapter)

    async def _sections_from_llm(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        """Ask the LLM to find section breaks in a chapter that has no h3 headings.

        Intent: a fallback for the messy case — the prompt returns titles + short
        verbatim excerpts; we string-match each excerpt to recover deterministic
        char offsets and emit one Section per matched excerpt. If nothing matches,
        the whole chapter becomes a single section.
        """
        response = await self._llm_client.call_structured(
            system=SectionsFallbackPrompt.SYSTEM,
            user=SectionsFallbackPrompt.build_user(spine_item, chapter),
            response_model=SectionsFallbackPrompt.RESPONSE_MODEL,
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

        Intent: shared by both "short chapter, no h3s" and "LLM returned no usable
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
