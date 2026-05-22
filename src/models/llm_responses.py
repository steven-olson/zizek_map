from typing import Literal

from pydantic import BaseModel, Field

SpineRole = Literal["front_matter", "part_divider", "chapter", "back_matter"]


class SpineItemClassification(BaseModel):
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
            "Drop publisher prefixes like 'Chapter 1:' if they're redundant with the actual title. "
            "Null for front_matter and back_matter."
        ),
    )
    parent_part_file_path: str | None = Field(
        default=None,
        description=(
            "Only set when role='chapter' AND the book is organized into parts. "
            "The file_path of the part_divider that contains this chapter."
        ),
    )


class SpineClassificationResponse(BaseModel):
    items: list[SpineItemClassification] = Field(
        description="One entry per spine item, in the same order as provided in the prompt."
    )


class SectionBreak(BaseModel):
    title: str = Field(description="Clean title of the section")
    first_sentence_excerpt: str = Field(
        description=(
            "An exact, verbatim excerpt (15-40 characters) from the very first sentence of this "
            "section's body text. Used to locate the section's starting character offset by "
            "string-matching against the chapter's plaintext. Must appear EXACTLY in the source."
        )
    )


class SectionsResponse(BaseModel):
    sections: list[SectionBreak] = Field(
        description="Sections within the chapter, in reading order."
    )
