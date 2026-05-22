from src.deps.epub_ingest import SpineItem
from src.models.text_components import Chapter


class SectionsFallbackPrompt:
    """System prompt + user-message builder for the 'find sections inside an unmarked
    chapter' LLM call. Used when a chapter has no explicit structural markers (no TOC
    fragments, no `<h3>` headings) but is long enough to plausibly have sub-structure.
    Holds no response model — the service pairs this with the matching response model
    when invoking the LLM client.
    """

    SYSTEM = """\
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

    @staticmethod
    def build_user(spine_item: SpineItem, chapter: Chapter) -> str:
        """Assemble the user message: chapter title + full chapter plaintext + instructions.

        Intent: give the model the entire chapter so it can find topic shifts, and
        anchor each section with a verbatim excerpt the service can string-match later.
        """
        return (
            f"Chapter title: {chapter.title}\n\n"
            "Identify the sections in the chapter text below. For each section, "
            "return its title and a short verbatim excerpt (15-40 chars) from its first "
            "sentence so I can locate it in the source.\n\n"
            "CHAPTER TEXT:\n"
            f"{spine_item.plaintext}"
        )
