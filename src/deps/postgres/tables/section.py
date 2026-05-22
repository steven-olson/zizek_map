import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base


class Section(Base):
    __tablename__ = "sections"

    title: Mapped[str] = mapped_column(String, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    spine_file_path: Mapped[str] = mapped_column(String, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_chapter_id: Mapped[str] = mapped_column(
        String, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
