import logging
from pathlib import Path

from src.deps.claude_client import ClaudeClient
from src.deps.epub_ingest import EpubIngestReader, HeadingMatch, ParsedEpub, SpineItem, TocNode
from src.models.llm_responses import (
    SectionsResponse,
    SpineClassificationResponse,
    SpineItemClassification,
    SpineRole,
)
from src.models.text_components import Book, BookStructuredComponents, Chapter, Part, Section

logger = logging.getLogger(__name__)

_SPINE_PREVIEW_CHARS = 300
_SECTION_FALLBACK_THRESHOLD = 4000


class BookComponentBreakdownError(RuntimeError):
    pass


class BookComponentBreakdownService:
    """Business logic: given a book on disk, return its structured components
    (book + parts + chapters + sections). Does not persist."""

    def __init__(self, epub_reader: EpubIngestReader, claude_client: ClaudeClient) -> None:
        """Hold the two deps this service composes: epub parsing and the LLM client.

        Intent: dependency injection so the workflow controls lifecycle and tests can
        swap either side with fakes without monkey-patching.
        """
        self._epub_reader = epub_reader
        self._claude_client = claude_client

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

        Intent: a single LLM round-trip that consumes the EPUB's TOC + per-spine previews
        and emits a classification per spine item. The deterministic spine ordering is
        preserved when we synthesize Parts and Chapters from the LLM's verdicts.
        """
        logger.info("classifying spine items=%d for book=%r", len(parsed.spine), parsed.title)

        user_prompt = self._build_classification_prompt(parsed)
        response = await self._claude_client.call_structured(
            system=_CLASSIFICATION_SYSTEM_PROMPT,
            user=user_prompt,
            response_model=SpineClassificationResponse,
        )
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
        """Ask Claude to find section breaks in a chapter that has no h3 headings.

        Intent: a fallback for the messy case — Claude returns titles + short verbatim
        excerpts, and we string-match each excerpt against the chapter plaintext to
        recover a deterministic char_start. Any excerpt that doesn't match is dropped.
        """
        user_prompt = (
            f"Chapter title: {chapter.title}\n\n"
            "Identify the sections in the chapter text below. For each section, "
            "return its title and a short verbatim excerpt (15-40 chars) from its first sentence "
            "so I can locate it in the source.\n\n"
            "CHAPTER TEXT:\n"
            f"{spine_item.plaintext}"
        )
        response = await self._claude_client.call_structured(
            system=_SECTION_FALLBACK_SYSTEM_PROMPT,
            user=user_prompt,
            response_model=SectionsResponse,
        )

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

    @staticmethod
    def _build_classification_prompt(parsed: ParsedEpub) -> str:
        """Render the per-book context Claude needs to classify spine items.

        Intent: bundle the TOC tree and a compact per-spine snapshot (file path,
        plaintext length, headings, short preview) into one user message — enough
        signal to distinguish front matter / part divider / chapter / back matter
        without sending the whole book text.
        """
        lines: list[str] = []
        lines.append(f"BOOK TITLE: {parsed.title}")
        if parsed.author:
            lines.append(f"AUTHOR: {parsed.author}")
        lines.append("")
        lines.append("TABLE OF CONTENTS (from the EPUB's toc.ncx):")
        for node in parsed.toc:
            BookComponentBreakdownService._format_toc_node(node, depth=0, out=lines)
        lines.append("")
        lines.append("SPINE ITEMS (in reading order):")
        for spine_item in parsed.spine:
            preview = spine_item.plaintext[:_SPINE_PREVIEW_CHARS].replace("\n", " ").strip()
            headings_summary = ", ".join(f"h{h.level}: {h.text!r}" for h in spine_item.headings[:5])
            lines.append(
                f"  - file_path: {spine_item.file_path}\n"
                f"    plaintext_chars: {len(spine_item.plaintext)}\n"
                f"    headings: [{headings_summary}]\n"
                f"    preview: {preview!r}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_toc_node(node: TocNode, depth: int, out: list[str]) -> None:
        """Append an indented textual representation of one TOC node + its descendants.

        Intent: keep the prompt's TOC section human-readable so Claude can see the
        Part → Chapter nesting at a glance and use it to populate `parent_part_file_path`.
        """
        indent = "  " * depth
        out.append(f"{indent}- {node.label!r} -> {node.file_path}")
        for child in node.children:
            BookComponentBreakdownService._format_toc_node(child, depth + 1, out)


_CLASSIFICATION_SYSTEM_PROMPT = """\
You classify the spine items of an EPUB book into their structural role.

You will receive:
1. The book's title and author.
2. The Table of Contents from the EPUB's toc.ncx (nested where the book has parts).
3. The full ordered list of spine items, each with: file_path, plaintext character count, the
   first few headings inside the file, and a short text preview.

Return one entry per spine item, IN THE SAME ORDER. For each, assign exactly one role:

  - "front_matter": cover page, half-title, full title page, copyright page, dedication,
    epigraph, table-of-contents page, preface (if very short / formulaic), acknowledgments
    that appear at the front.
  - "part_divider": a Part-level grouping (e.g. "Part I: Beyond the Transcendental") that
    has its own dedicated spine file. Typically very short — just a part label/title.
    These appear as parents over chapters in the TOC.
  - "chapter": a substantive unit of the book's content. Numbered chapters, interludes,
    introductions/forewords with real content, conclusions, and afterwords all count as
    chapters. Anything that contains the author's actual argument/material.
  - "back_matter": index, appendices, bibliography, notes (if separated out), about-the-author,
    other-titles, ads.

For role="chapter" and role="part_divider", populate `clean_title` with a display-ready title.
Strip publisher prefixes like "Chapter 1:" when the rest is a real title (e.g.
"Chapter 1: Towards a Materialist Theory of Subjectivity" -> "Towards a Materialist Theory of
Subjectivity"). For interludes, keep them named clearly (e.g. "Interlude I: Staging Feminine
Hysteria"). For null roles, leave `clean_title` null.

For role="chapter": if the TOC shows the chapter is nested under a part_divider, set
`parent_part_file_path` to that part_divider's file_path. If the book has no parts, leave it null.

Be conservative — when in doubt between front_matter and chapter, pick chapter only if there
is substantial body content (the plaintext_chars count and preview will indicate this).
"""

_SECTION_FALLBACK_SYSTEM_PROMPT = """\
You are given the full plaintext of a single chapter from a book that does NOT have explicit
sub-section headings. Identify the natural section breaks within the chapter (topic shifts,
new arguments, narrative pivots) and return them in reading order.

For each section: provide a clean, short title (4-8 words) and `first_sentence_excerpt` — a
short (15-40 character) verbatim slice from the very first sentence of that section's body
text. The excerpt MUST appear EXACTLY in the chapter text I provided so I can locate the
section by string-matching.

If the chapter has no clear sub-structure, return an empty `sections` list and I will treat
the chapter as a single section.
"""
