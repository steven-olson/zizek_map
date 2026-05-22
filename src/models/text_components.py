import uuid

from pydantic import BaseModel, Field


class Book(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Title of the book")
    author: str | None = Field(default=None, description="Author of the book if known")
    file_path: str = Field(description="Absolute path to the source EPUB file")


class Part(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Clean title of the part, e.g. 'Beyond the Transcendental'")
    order_index: int = Field(description="Zero-based order of this part within the book")
    parent_book_id: str = Field(description="ID of the parent Book")


class Chapter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Clean title of the chapter")
    order_index: int = Field(description="Zero-based order of this chapter within the book")
    spine_file_path: str = Field(
        description="Path of the EPUB spine item that contains this chapter, e.g. 'OEBPS/08_Chapter1.xhtml'"
    )
    char_start: int = Field(
        description="Character offset (inclusive) into the spine item's plaintext"
    )
    char_end: int = Field(
        description="Character offset (exclusive) into the spine item's plaintext"
    )
    parent_book_id: str = Field(description="ID of the parent Book")
    parent_part_id: str | None = Field(
        default=None, description="ID of the parent Part, or None if the book has no parts"
    )


class Section(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Clean title of the section")
    order_index: int = Field(description="Zero-based order of this section within its chapter")
    spine_file_path: str = Field(
        description="Path of the EPUB spine item that contains this section"
    )
    char_start: int = Field(
        description="Character offset (inclusive) into the spine item's plaintext"
    )
    char_end: int = Field(
        description="Character offset (exclusive) into the spine item's plaintext"
    )
    parent_chapter_id: str = Field(description="ID of the parent Chapter")


class BookSkeleton(BaseModel):
    book: Book
    parts: list[Part] = Field(default_factory=list, description="Empty when the book has no parts")
    chapters: list[Chapter]


class BookStructuredComponents(BaseModel):
    book: Book
    parts: list[Part] = Field(default_factory=list, description="Empty when the book has no parts")
    chapters: list[Chapter]
    sections: list[Section]
