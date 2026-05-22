import logging
from dataclasses import dataclass

from sqlalchemy import delete, select

from src.deps.postgres.database import Database
from src.deps.postgres.tables import Book, Chapter, Part, Section

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookStructure:
    book: Book
    parts: list[Part]
    chapters: list[Chapter]
    sections_by_chapter: dict[str, list[Section]]


class BookComponentRepository:
    """Persistence facade for Book + Part + Chapter + Section.

    Intent: every Postgres call about book components lives here so the UI and
    the ingest pipeline never see an `AsyncSession` or a `select(...)`. Changes
    to the storage layer stop at this boundary.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_breakdown(
        self,
        book: Book,
        parts: list[Part],
        chapters: list[Chapter],
        sections: list[Section],
    ) -> None:
        """Persist a full ingest in one transaction (book + parts + chapters + sections).

        Intent: all-or-nothing — if any insert fails, the whole tree rolls back so
        Postgres never holds a partially-ingested book.
        """
        logger.info(
            "saving breakdown book_id=%s parts=%d chapters=%d sections=%d",
            book.id,
            len(parts),
            len(chapters),
            len(sections),
        )
        async with self._db.session() as session:
            session.add(book)
            session.add_all(parts)
            session.add_all(chapters)
            session.add_all(sections)
            await session.commit()

    async def find_by_file_path(self, file_path: str) -> Book | None:
        """Return the Book row with this file_path, or None."""
        async with self._db.session() as session:
            result = await session.execute(select(Book).where(Book.file_path == file_path))
            return result.scalar_one_or_none()

    async def list_ingested_paths(self) -> set[str]:
        """Return the set of `file_path` values already present in the books table."""
        async with self._db.session() as session:
            result = await session.execute(select(Book.file_path))
            return set(result.scalars().all())

    async def list_books(self) -> list[Book]:
        """Return every Book in `created_at` order — used by the ingested-books screen."""
        async with self._db.session() as session:
            result = await session.execute(select(Book).order_by(Book.created_at))
            return list(result.scalars().all())

    async def load_structure(self, book_id: str) -> BookStructure | None:
        """Return the full hierarchy (parts → chapters → sections) for one Book.

        Three small queries in one session — keeps the right-hand tree's render
        atomic without joining all four tables in one big query.
        """
        async with self._db.session() as session:
            book = await session.get(Book, book_id)
            if book is None:
                return None
            parts = list(
                (
                    await session.execute(
                        select(Part)
                        .where(Part.parent_book_id == book_id)
                        .order_by(Part.order_index)
                    )
                )
                .scalars()
                .all()
            )
            chapters = list(
                (
                    await session.execute(
                        select(Chapter)
                        .where(Chapter.parent_book_id == book_id)
                        .order_by(Chapter.order_index)
                    )
                )
                .scalars()
                .all()
            )
            sections_by_chapter: dict[str, list[Section]] = {}
            if chapters:
                section_rows = (
                    await session.execute(
                        select(Section)
                        .where(Section.parent_chapter_id.in_([c.id for c in chapters]))
                        .order_by(Section.parent_chapter_id, Section.order_index)
                    )
                ).scalars()
                for s in section_rows:
                    sections_by_chapter.setdefault(s.parent_chapter_id, []).append(s)
        return BookStructure(
            book=book,
            parts=parts,
            chapters=chapters,
            sections_by_chapter=sections_by_chapter,
        )

    async def delete_cascade(self, book_id: str) -> None:
        """Delete the Book by id; ON DELETE CASCADE wipes its parts/chapters/sections.

        Intent: used by the ingest pipeline when re-ingesting a file whose content
        hash has changed — we want a clean tree, not a merge.
        """
        logger.info("deleting book and descendants book_id=%s", book_id)
        async with self._db.session() as session:
            await session.execute(delete(Book).where(Book.id == book_id))
            await session.commit()
