import uuid

from pydantic import BaseModel, Field


class SectionSummary(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID fpr this summary",
    )
    summary: str = Field(
        description="Text containing a useful summary of the corresponding part of the text"
    )
    section_id: str = Field(description="Unique ID of the text section this is a summary of")


class ChapterSummary(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID fpr this summary",
    )
    summary: str = Field(
        description="Text containing a useful summary of the corresponding part of the text"
    )
    chapter_id: str = Field(description="Unique ID of the text chapter this is a summary of")


class BookSummary(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique ID fpr this summary",
    )
    summary: str = Field(
        description="Text containing a useful summary of the corresponding part of the text"
    )
    chapter_id: str = Field(description="Unique ID of the book this is a summary of")
