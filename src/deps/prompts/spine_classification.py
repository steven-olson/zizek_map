from src.deps.epub_ingest import ParsedEpub, TocNode
from src.deps.llm_client import LlmClient
from src.models.llm_responses import SpineClassificationResponse

_SPINE_PREVIEW_CHARS = 300


class SpineClassificationPrompt:
    """Bundle for the 'classify EPUB spine items into structural roles' LLM call.

    Pairs the system prompt, the expected response model, and the per-call user-prompt
    builder so the service only has to call `execute(...)` — never to assemble strings
    or know the response schema.
    """

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

    RESPONSE_MODEL = SpineClassificationResponse

    @classmethod
    async def execute(cls, llm: LlmClient, parsed: ParsedEpub) -> SpineClassificationResponse:
        """Run the classification call end-to-end against the given LLM.

        Intent: the service calls this once per ingest and receives a typed response
        ready for downstream synthesis; no prompt strings escape this class.
        """
        return await llm.call_structured(
            system=cls.SYSTEM,
            user=cls._build_user(parsed),
            response_model=cls.RESPONSE_MODEL,
        )

    @staticmethod
    def _build_user(parsed: ParsedEpub) -> str:
        """Assemble the user-message context Claude needs to classify every spine item.

        Intent: bundle the TOC tree and a compact per-spine snapshot (file path,
        plaintext length, headings, short preview) into one message — enough signal to
        distinguish front matter / part divider / chapter / back matter without sending
        the whole book text.
        """
        lines: list[str] = []
        lines.append(f"BOOK TITLE: {parsed.title}")
        if parsed.author:
            lines.append(f"AUTHOR: {parsed.author}")
        lines.append("")
        lines.append("TABLE OF CONTENTS (from the EPUB's toc.ncx):")
        for node in parsed.toc:
            SpineClassificationPrompt._format_toc_node(node, depth=0, out=lines)
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
        """Append an indented textual rendering of one TOC node + its descendants.

        Intent: keep the prompt's TOC section human-readable so the model can see the
        Part → Chapter nesting at a glance and use it to populate `parent_part_file_path`.
        """
        indent = "  " * depth
        out.append(f"{indent}- {node.label!r} -> {node.file_path}")
        for child in node.children:
            SpineClassificationPrompt._format_toc_node(child, depth + 1, out)
