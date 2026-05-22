import logging
from pathlib import Path

from src.deps.epub_ingest import EpubIngestReader, HeadingMatch, ParsedEpub, SpineItem
from src.deps.llm_client import LlmClient
from src.deps.prompts.sections_fallback import SectionsFallbackPrompt
from src.deps.prompts.spine_classification import SpineClassificationPrompt
from src.models.text_components import Book, BookStructuredComponents, Chapter, Part, Section

logger = logging.getLogger(__name__)

_SECTION_FALLBACK_THRESHOLD = 4000


class BookComponentBreakdownError(RuntimeError):
    pass


class BookComponentBreakdownService:
    """Business logic: given a book on disk, return its structured components
    (book + parts + chapters + sections). Does not persist."""

    def __init__(self, epub_reader: EpubIngestReader, llm_client: LlmClient) -> None:
        """Hold the two deps this service composes: epub parsing and the LLM client.

        Intent: dependency injection so the workflow controls lifecycle and tests can
        swap either side with fakes without monkey-patching.
        """
        self._epub_reader = epub_reader
        self._llm_client = llm_client

    async def get_book_components(self, book_epub_path: Path) -> BookStructuredComponents:
        """One-shot end-to-end: parse the epub and return its full BookStructuredComponents.

        Intent: convenience entry point for non-UI callers (scripts, tests) that don't
        need per-chapter progress reporting — the Textual workflow drives the two
        sub-steps individually so it can yield events between them.
        """
        parsed = self._epub_reader.read(book_epub_path)
        book, parts, chapters = await self.classify_book(parsed, str(book_epub_path))
        sections = await self.resolve_sections(parsed, chapters)
        return BookStructuredComponents(
            book=book, parts=parts, chapters=chapters, sections=sections
        )

    async def classify_book(
        self, parsed: ParsedEpub, file_path: str
    ) -> tuple[Book, list[Part], list[Chapter]]:
        """Decide which spine items are real chapters/parts and build domain objects for them.

        Intent: a single LLM round-trip (delegated to SpineClassificationPrompt) that
        consumes the EPUB's TOC + per-spine previews and emits a classification per
        spine item. The deterministic spine ordering is preserved when we synthesize
        Parts and Chapters from the LLM's verdicts.
        """
        logger.info("classifying spine items=%d for book=%r", len(parsed.spine), parsed.title)

        response = await SpineClassificationPrompt.execute(self._llm_client, parsed)
        classifications_by_path = {item.file_path: item for item in response.items}

        book = Book(title=parsed.title, author=parsed.author, file_path=file_path)

        parts: list[Part] = []
        part_id_by_file_path: dict[str, str] = {}
        part_order = 0
        for spine_item in parsed.spine:
            cls = classifications_by_path.get(spine_item.file_path)
            if cls is None or cls.role != "part_divider":
                continue
            part = Part(
                title=(cls.clean_title or spine_item.file_path).strip(),
                order_index=part_order,
                parent_book_id=book.id,
            )
            parts.append(part)
            part_id_by_file_path[spine_item.file_path] = part.id
            part_order += 1

        chapters: list[Chapter] = []
        chapter_order = 0
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
                    order_index=chapter_order,
                    spine_file_path=spine_item.file_path,
                    char_start=0,
                    char_end=len(spine_item.plaintext),
                    parent_book_id=book.id,
                    parent_part_id=parent_part_id,
                )
            )
            chapter_order += 1

        logger.info(
            "classification produced book=%s parts=%d chapters=%d",
            book.id,
            len(parts),
            len(chapters),
        )
        return book, parts, chapters

    async def resolve_sections(self, parsed: ParsedEpub, chapters: list[Chapter]) -> list[Section]:
        """Resolve sections across every chapter in one go.

        Intent: convenience for callers that don't care about per-chapter progress —
        the per-chapter form is the unit the workflow loops over for streaming updates.
        """
        sections: list[Section] = []
        for chapter in chapters:
            chapter_sections = await self.resolve_sections_for_chapter(parsed, chapter)
            sections.extend(chapter_sections)
        logger.info("resolved sections total=%d", len(sections))
        return sections

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
        return await self._sections_for_chapter(spine_item, chapter)

    async def _sections_for_chapter(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        """Decide between deterministic-h3 / single-section / LLM-fallback for one chapter.

        Intent: choose the cheapest strategy that still captures real structure. h3
        headings are the EPUB's own ground truth; the LLM only gets called when there
        are no headings AND the chapter is long enough to plausibly have sub-structure.
        """
        h3s = [h for h in spine_item.headings if h.level == 3]
        if h3s:
            return self._sections_from_headings(h3s, spine_item, chapter)
        if len(spine_item.plaintext) < _SECTION_FALLBACK_THRESHOLD:
            logger.info(
                "chapter=%r is short and has no h3s; emitting single section",
                chapter.title,
            )
            return [
                Section(
                    title=chapter.title,
                    order_index=0,
                    spine_file_path=spine_item.file_path,
                    char_start=chapter.char_start,
                    char_end=chapter.char_end,
                    parent_chapter_id=chapter.id,
                )
            ]
        logger.info("chapter=%r has no h3s; falling back to LLM section detection", chapter.title)
        return await self._sections_from_llm(spine_item, chapter)

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
        sections: list[Section] = []
        for idx, heading in enumerate(h3s):
            char_start = heading.char_offset
            char_end = h3s[idx + 1].char_offset if idx + 1 < len(h3s) else chapter.char_end
            sections.append(
                Section(
                    title=heading.text,
                    order_index=idx,
                    spine_file_path=spine_item.file_path,
                    char_start=char_start,
                    char_end=char_end,
                    parent_chapter_id=chapter.id,
                )
            )
        return sections

    async def _sections_from_llm(self, spine_item: SpineItem, chapter: Chapter) -> list[Section]:
        """Ask the LLM to find section breaks in a chapter that has no h3 headings.

        Intent: a fallback for the messy case — the prompt class returns titles +
        short verbatim excerpts, and we string-match each excerpt against the chapter
        plaintext to recover a deterministic char_start. Any excerpt that doesn't
        match is dropped.
        """
        response = await SectionsFallbackPrompt.execute(self._llm_client, spine_item, chapter)

        offsets: list[tuple[str, int]] = []
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
            offsets.append((sb.title, offset))

        if not offsets:
            return [
                Section(
                    title=chapter.title,
                    order_index=0,
                    spine_file_path=spine_item.file_path,
                    char_start=chapter.char_start,
                    char_end=chapter.char_end,
                    parent_chapter_id=chapter.id,
                )
            ]

        offsets.sort(key=lambda t: t[1])
        sections: list[Section] = []
        for idx, (title, offset) in enumerate(offsets):
            char_end = offsets[idx + 1][1] if idx + 1 < len(offsets) else chapter.char_end
            sections.append(
                Section(
                    title=title,
                    order_index=idx,
                    spine_file_path=spine_item.file_path,
                    char_start=offset,
                    char_end=char_end,
                    parent_chapter_id=chapter.id,
                )
            )
        return sections
