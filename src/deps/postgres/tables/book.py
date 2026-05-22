import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.deps.postgres.database import Base


class Book(Base):
    __tablename__ = "books"

    title: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    author: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    file_hash: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    id: Mapped[str] = mapped_column(
        String, primary_key=True, default_factory=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        init=False,
    )
