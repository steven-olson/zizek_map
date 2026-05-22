import re

from src.deps.epub_ingest import ParsedEpub, SpineItem, TocNode

_SPINE_PREVIEW_CHARS = 300
_DRM_WATERMARK_RE = re.compile(r"\s*This eBook is licensed to.*", re.IGNORECASE | re.DOTALL)


class SpineClassificationPrompt:
    """System prompt + user-message builder for the 'classify EPUB spine items into
    structural roles' LLM call. Holds no response model and no call wiring — the
    service pairs this with the matching response model when invoking the LLM client."""

    SYSTEM = """\
You classify the spine items of an EPUB book into their structural role.

You will receive:
1. The book's title and author.
2. The Table of Contents from the EPUB's toc.ncx (nested where the book has parts; TOC
   entries may include a #fragment when pointing at an in-document anchor).
3. A list of spine items to classify (some boilerplate spine items have been pre-filtered
   out via the TOC, so the list you see may already exclude pure noise like footnote files).
   Each spine item has: file_path, plaintext character count, the first few headings inside
   the file, and a short text preview.

Return one entry per spine item, IN THE SAME ORDER. For each, assign exactly one role:

  - "front_matter": cover page, half-title, full title page, copyright page, dedication,
    epigraph, table-of-contents page, preface (if very short / formulaic), acknowledgments
    that appear at the front.
  - "part_divider": a Part-level grouping (e.g. "Part I: Beyond the Transcendental", or
    "PART I. THE DRINK BEFORE") that has its own dedicated spine file. Typically very short
    — just a part label/title. These appear as parents over chapters in the TOC.
  - "chapter": a substantive unit of the book's content. Numbered chapters, interludes,
    introductions/forewords with real content, conclusions, and afterwords all count as
    chapters. Anything that contains the author's actual argument/material.
  - "back_matter": index, appendices, bibliography, notes (if separated out), about-the-author,
    other-titles, ads, DRM disclaimers.

For role="chapter" and role="part_divider", populate `clean_title` with a display-ready title.
Strip publisher prefixes (chapter/part numbers, "Chapter N:", "PART N.", bare leading digits)
when the rest is a real title. Examples:

  - "Chapter 1: Towards a Materialist Theory of Subjectivity" -> "Towards a Materialist Theory of Subjectivity"
  - "1 \"Vacillating the Semblances\"" -> "Vacillating the Semblances"
  - "1How Did Marx Invent the Symptom?" -> "How Did Marx Invent the Symptom?"
  - "PART I. THE DRINK BEFORE" -> "The Drink Before"
  - "Part II: The Hegelian Event" -> "The Hegelian Event"

For interludes, keep them named clearly (e.g. "Interlude I: Staging Feminine Hysteria",
"Interlude 1: Marx as a Reader of Hegel"). For "Conclusion: ..." chapters keep the prefix.
For null roles, leave `clean_title` null.

For role="chapter": if the TOC shows the chapter is nested under a part_divider, set
`parent_part_file_path` to that part_divider's file_path (bare path, no #fragment).
If the book has no parts, leave it null.

Be conservative — when in doubt between front_matter and chapter, pick chapter only if there
is substantial body content (the plaintext_chars count and preview will indicate this).
"""

    @staticmethod
    def build_user(parsed: ParsedEpub, spine_items: list[SpineItem]) -> str:
        """Assemble the user-message context Claude needs to classify the given spine items.

        Intent: bundle the TOC tree and a compact per-spine snapshot (file path,
        plaintext length, headings, short preview) into one message — enough signal to
        distinguish front matter / part divider / chapter / back matter without sending
        the whole book text. `spine_items` is the (possibly pre-filtered) subset of
        `parsed.spine` actually being classified.
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
        for spine_item in spine_items:
            raw_preview = spine_item.plaintext[:_SPINE_PREVIEW_CHARS].replace("\n", " ").strip()
            preview = _DRM_WATERMARK_RE.sub("", raw_preview).strip()
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
        Fragments are shown when present so the model understands when a TOC entry points
        at a section inside a chapter file rather than a whole-file boundary.
        """
        indent = "  " * depth
        target = node.file_path or "-"
        if node.fragment:
            target = f"{target}#{node.fragment}"
        out.append(f"{indent}- {node.label!r} -> {target}")
        for child in node.children:
            SpineClassificationPrompt._format_toc_node(child, depth + 1, out)
