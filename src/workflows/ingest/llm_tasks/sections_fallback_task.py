import hashlib
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from src.deps.epub_ingest import SpineItem
from src.deps.llm.client import LlmCaller
from src.deps.postgres.tables import Chapter, Section

logger = logging.getLogger(__name__)


class _SectionBreak(BaseModel):
    title: str = Field(description="Clean title of the section")
    first_sentence_excerpt: str = Field(
        description=(
            "An exact, verbatim excerpt (15-40 characters) from the very first sentence of this "
            "section's body text. Used to locate the section's starting character offset by "
            "string-matching against the chapter's plaintext. Must appear EXACTLY in the source."
        )
    )


class _SectionsResponse(BaseModel):
    sections: list[_SectionBreak] = Field(
        description="Sections within the chapter, in reading order."
    )


@dataclass(frozen=True)
class SectionsFallbackInput:
    spine_item: SpineItem
    chapter: Chapter
    file_hash: str


class SectionsFallbackTask:
    """LLM phase: find section breaks inside a chapter that has no `<h3>` headings.

    Returns a sorted list of `(title, char_offset)` locations relative to the spine
    item's plaintext. Excerpts that don't appear verbatim in the source are dropped
    with a warning. Building Section ORM rows from these tuples is the strategy's job.
    """

    VERSION = "v1"

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

    @classmethod
    async def execute(cls, llm: LlmCaller, inp: SectionsFallbackInput) -> list[tuple[str, int]]:
        """Return sorted `(title, char_offset)` tuples for sections found in the chapter.

        Intent: the strategy converts these tuples into Section ORM rows; this task
        owns only "ask the LLM, locate excerpts" so the cache key, prompt, schema,
        and post-processing all live in one place.
        """
        logger.info(
            "sections fallback start chapter=%r plaintext_chars=%d",
            inp.chapter.title,
            len(inp.spine_item.plaintext),
        )
        result = await llm.call_structured(
            system=cls.SYSTEM,
            user=cls._build_user(inp.spine_item, inp.chapter),
            response_model=_SectionsResponse,
            cache_key=cls._cache_key(inp),
        )
        return cls._locate_excerpts(result.output, inp.spine_item, inp.chapter)

    @classmethod
    def _cache_key(cls, inp: SectionsFallbackInput) -> str:
        """Per-chapter key — folds the file hash and the chapter's spine file path so
        re-runs on the same book hit the cache, but changes to the chapter content
        (different file hash) invalidate naturally."""
        chapter_token = hashlib.sha256(
            f"{inp.chapter.spine_file_path}:{inp.chapter.char_start}:{inp.chapter.char_end}".encode()
        ).hexdigest()[:16]
        return f"{cls.__name__}:{cls.VERSION}:{inp.file_hash}:{chapter_token}"

    @staticmethod
    def _locate_excerpts(
        response: _SectionsResponse, spine_item: SpineItem, chapter: Chapter
    ) -> list[tuple[str, int]]:
        """Resolve each LLM-suggested section to a `(title, char_offset)` via string-match.

        Excerpts that don't appear verbatim in the plaintext are dropped with a warning.
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
    def _build_user(spine_item: SpineItem, chapter: Chapter) -> str:
        return (
            f"Chapter title: {chapter.title}\n\n"
            "Identify the sections in the chapter text below. For each section, "
            "return its title and a short verbatim excerpt (15-40 chars) from its first "
            "sentence so I can locate it in the source.\n\n"
            "CHAPTER TEXT:\n"
            f"{spine_item.plaintext}"
        )


def build_sections_from_offsets(
    offsets: list[tuple[str, int]], spine_item: SpineItem, chapter: Chapter
) -> list[Section]:
    """Turn a sorted list of `(title, char_start)` tuples into Section ORM rows.

    Each section's `char_end` is the next section's start, or the chapter end for the
    last entry — sections tile the chapter without gaps or overlaps.
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
