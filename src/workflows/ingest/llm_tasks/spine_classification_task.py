import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from src.deps.epub_ingest import ParsedEpub, TocNode
from src.deps.llm.client import LlmCaller
from src.deps.postgres.tables import Book, Chapter, Part

logger = logging.getLogger(__name__)

_SPINE_PREVIEW_CHARS = 300


SpineRole = Literal["front_matter", "part_divider", "chapter", "back_matter"]


class _SpineItemClassification(BaseModel):
    file_path: str = Field(description="The spine item's file path, e.g. 'OEBPS/08_Chapter1.xhtml'")
    role: SpineRole = Field(
        description=(
            "What this spine item is: 'front_matter' (cover, title, copyright, dedication, "
            "table of contents, etc.), 'part_divider' (a Part-level grouping that has its own "
            "page), 'chapter' (a numbered chapter, interlude, or introduction with substantial "
            "content), or 'back_matter' (index, appendix, bibliography, etc.)."
        )
    )
    clean_title: str | None = Field(
        default=None,
        description=(
            "Cleaned-up title suitable for display, for chapters and part_dividers only. "
            "Drop publisher prefixes like 'Chapter 1:' if they're redundant with the actual title."
        ),
    )
    parent_part_file_path: str | None = Field(
        default=None,
        description=(
            "Only set when role='chapter' AND the book is organized into parts. "
            "The file_path of the part_divider that contains this chapter."
        ),
    )


class _SpineClassificationResponse(BaseModel):
    items: list[_SpineItemClassification] = Field(
        description="One entry per spine item, in the same order as provided in the prompt."
    )


@dataclass(frozen=True)
class SpineClassificationInput:
    parsed: ParsedEpub
    file_path: str
    file_hash: str


@dataclass(frozen=True)
class SpineClassificationOutput:
    book: Book
    parts: list[Part]
    chapters: list[Chapter]


class SpineClassificationTask:
    """One self-contained LLM-driven phase: classify EPUB spine items into structural
    roles and synthesize Book + Parts + Chapters from the result.

    Bundles everything that defines this LLM call as one unit — the system prompt,
    a stable `VERSION` for cache invalidation, the response schema, the user-message
    renderer, and the post-processing that turns the typed response into ORM dataclass
    instances. Callers see a single typed `execute(llm_tasks, input) -> output` interface.
    """

    VERSION = "v1"

    SYSTEM = """\
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

    @classmethod
    async def execute(
        cls, llm: LlmCaller, inp: SpineClassificationInput
    ) -> SpineClassificationOutput:
        """Run the full LLM phase end-to-end: render prompt, call LLM, synthesize output.

        Intent: the pipeline step calls this once; cache lookup, parsing, and ORM
        construction are all internal concerns of this task.
        """
        logger.info(
            "spine classification start file_path=%s spine_items=%d",
            inp.file_path,
            len(inp.parsed.spine),
        )
        result = await llm.call_structured(
            system=cls.SYSTEM,
            user=cls._build_user(inp.parsed),
            response_model=_SpineClassificationResponse,
            cache_key=cls._cache_key(inp),
        )
        return cls._post_process(result.output, inp)

    @classmethod
    def _cache_key(cls, inp: SpineClassificationInput) -> str:
        """Deterministic key per (task, version, file content) — invalidates on any
        prompt change (bump VERSION) or any input file change (different hash)."""
        return f"{cls.__name__}:{cls.VERSION}:{inp.file_hash}"

    @classmethod
    def _post_process(
        cls, response: _SpineClassificationResponse, inp: SpineClassificationInput
    ) -> SpineClassificationOutput:
        """Synthesize Book + Parts + Chapters in deterministic spine order from the
        LLM's role-per-spine-item verdicts."""
        classifications_by_path = {item.file_path: item for item in response.items}
        book = Book(
            title=inp.parsed.title,
            file_path=inp.file_path,
            author=inp.parsed.author,
            file_hash=inp.file_hash,
        )
        parts, part_id_by_file_path = cls._build_parts(inp.parsed, classifications_by_path, book.id)
        chapters = cls._build_chapters(
            inp.parsed, classifications_by_path, book.id, part_id_by_file_path
        )
        logger.info(
            "spine classification done book_id=%s parts=%d chapters=%d",
            book.id,
            len(parts),
            len(chapters),
        )
        return SpineClassificationOutput(book=book, parts=parts, chapters=chapters)

    @staticmethod
    def _build_parts(
        parsed: ParsedEpub,
        classifications_by_path: dict[str, _SpineItemClassification],
        book_id: str,
    ) -> tuple[list[Part], dict[str, str]]:
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

    @staticmethod
    def _build_chapters(
        parsed: ParsedEpub,
        classifications_by_path: dict[str, _SpineItemClassification],
        book_id: str,
        part_id_by_file_path: dict[str, str],
    ) -> list[Chapter]:
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

    @staticmethod
    def _build_user(parsed: ParsedEpub) -> str:
        """Assemble the user-message context: TOC tree + per-spine snapshot.

        Bundles enough signal (file path, plaintext length, headings, short preview)
        to classify every spine item without sending the whole book text.
        """
        lines: list[str] = []
        lines.append(f"BOOK TITLE: {parsed.title}")
        if parsed.author:
            lines.append(f"AUTHOR: {parsed.author}")
        lines.append("")
        lines.append("TABLE OF CONTENTS (from the EPUB's toc.ncx):")
        for node in parsed.toc:
            SpineClassificationTask._format_toc_node(node, depth=0, out=lines)
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
        indent = "  " * depth
        out.append(f"{indent}- {node.label!r} -> {node.file_path}")
        for child in node.children:
            SpineClassificationTask._format_toc_node(child, depth + 1, out)
