import uuid
from typing import List

from pydantic import BaseModel, Field


class Book(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Title of the book")
    file_path: str = Field(description="File path of the book, ie where its saved")


class Chapter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Title of the chapter")
    page_start: int = Field(description="Which page this started on")
    page_end: int = Field(description="Which page this ended on")
    parent_book_id: str = Field(description="Parent book id")


class Section(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(description="Title of the section")
    page_start: int = Field(description="Which page this started on")
    page_end: int = Field(description="Which page this ended on")
    parent_chapter_id: str = Field(description="Parent chapter id")


class BookStructuredComponents(BaseModel):
    book: Book = Field(description="Book")
    chapters: List[Chapter] = Field(description="Chapters")
    sections: List[Section] = Field(description="Sections")