from datetime import datetime
from typing import Self

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base
from src.models.text_components import Book


class BookRow(Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @classmethod
    def from_pydantic(cls, book: Book) -> Self:
        """Project a domain `Book` into its ORM row form for persistence.

        Intent: keep the Pydantic side (LLM/domain shape) and the ORM side (DB shape)
        as two distinct layers, with this one tiny mapper as the bridge — no field
        is computed here, only renamed/typed for the DB.
        """
        return cls(
            id=book.id,
            title=book.title,
            author=book.author,
            file_path=book.file_path,
        )
