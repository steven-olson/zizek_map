from datetime import datetime
from typing import Self

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base
from src.models.text_components import Part


class PartRow(Base):
    __tablename__ = "parts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @classmethod
    def from_pydantic(cls, part: Part) -> Self:
        """Project a domain `Part` into its ORM row form for persistence.

        Intent: thin field-by-field copy so the workflow can hand the persistence
        layer a list of ORM rows without leaking SQLAlchemy specifics into services.
        """
        return cls(
            id=part.id,
            title=part.title,
            order_index=part.order_index,
            parent_book_id=part.parent_book_id,
        )
