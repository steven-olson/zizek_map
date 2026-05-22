from datetime import datetime
from typing import Self

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base
from src.models.text_components import Section


class SectionRow(Base):
    __tablename__ = "sections"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    spine_file_path: Mapped[str] = mapped_column(String, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_chapter_id: Mapped[str] = mapped_column(
        String, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @classmethod
    def from_pydantic(cls, section: Section) -> Self:
        """Project a domain `Section` into its ORM row form for persistence.

        Intent: stores the precise plaintext slice (`char_start`/`char_end`) that defines
        this section so summarization can later read it back without re-running the LLM
        classification step.
        """
        return cls(
            id=section.id,
            title=section.title,
            order_index=section.order_index,
            spine_file_path=section.spine_file_path,
            char_start=section.char_start,
            char_end=section.char_end,
            parent_chapter_id=section.parent_chapter_id,
        )
