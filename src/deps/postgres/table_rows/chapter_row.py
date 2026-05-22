from datetime import datetime
from typing import Self

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base
from src.models.text_components import Chapter


class ChapterRow(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    spine_file_path: Mapped[str] = mapped_column(String, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    parent_part_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("parts.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @classmethod
    def from_pydantic(cls, chapter: Chapter) -> Self:
        """Project a domain `Chapter` into its ORM row form for persistence.

        Intent: preserves the `(spine_file_path, char_start, char_end)` triple verbatim
        so part 2 can re-slice the same plaintext range when generating summaries.
        """
        return cls(
            id=chapter.id,
            title=chapter.title,
            order_index=chapter.order_index,
            spine_file_path=chapter.spine_file_path,
            char_start=chapter.char_start,
            char_end=chapter.char_end,
            parent_book_id=chapter.parent_book_id,
            parent_part_id=chapter.parent_part_id,
        )
