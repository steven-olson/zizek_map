from src.deps.epub_ingest import SpineItem
from src.deps.llm_client import LlmClient
from src.models.llm_responses import SectionsResponse
from src.models.text_components import Chapter


class SectionsFallbackPrompt:
    """Bundle for the 'find sections inside an unmarked chapter' LLM call.

    Used only when a chapter has no `<h3>` headings AND its plaintext is long enough
    to plausibly have sub-structure — pairs the system prompt, response model, and
    user-prompt builder so the service is one `execute(...)` call away from results.
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

    RESPONSE_MODEL = SectionsResponse

    @classmethod
    async def execute(
        cls, llm: LlmClient, spine_item: SpineItem, chapter: Chapter
    ) -> SectionsResponse:
        """Run the section-fallback call end-to-end against the given LLM.

        Intent: keep all prompt-rendering concerns inside this class; the service only
        needs to pass in the spine item + chapter and consume the typed response.
        """
        return await llm.call_structured(
            system=cls.SYSTEM,
            user=cls._build_user(spine_item, chapter),
            response_model=cls.RESPONSE_MODEL,
        )

    @staticmethod
    def _build_user(spine_item: SpineItem, chapter: Chapter) -> str:
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
