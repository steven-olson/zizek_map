import logging
from pathlib import Path

from src.deps.epub_ingest import EpubIngestReader
from src.deps.postgres.database import Database
from src.deps.postgres.table_rows import BookRow, ChapterRow, PartRow, SectionRow
from src.models.text_components import BookStructuredComponents, Section
from src.services.book_skeleton_service import BookSkeletonService
from src.services.chapter_sectioning_service import ChapterSectioningService

logger = logging.getLogger(__name__)


class BookIngestWorkflow:
    """Coordinator for end-to-end book ingestion.

    Lightweight by design: parses the EPUB, calls the skeleton service for the
    chapter/part structure, calls the sectioning service per chapter for section
    boundaries, assembles the result, and persists. Holds no business logic itself —
    every decision lives in the services it composes.
    """

    def __init__(
        self,
        epub_reader: EpubIngestReader,
        skeleton_service: BookSkeletonService,
        sectioning_service: ChapterSectioningService,
        db: Database,
    ) -> None:
        """Stash the deps and services this workflow composes.

        Intent: a single shared instance is constructed in `run()` (app entry point)
        and reused across ingests — the `run(book_path)` method takes the per-book
        input rather than the constructor.
        """
        self._epub_reader = epub_reader
        self._skeleton_service = skeleton_service
        self._sectioning_service = sectioning_service
        self._db = db

    async def run(self, book_path: Path) -> BookStructuredComponents:
        """Ingest one EPUB end-to-end and return the persisted structure.

        Intent: a single linear pipeline — parse → skeleton → per-chapter sections →
        assemble → persist. Failures propagate to the caller; persistence is one
        transaction so a crash leaves Postgres untouched.
        """
        logger.info("ingest start book_path=%s", book_path)

        parsed = self._epub_reader.read(book_path)
        skeleton = await self._skeleton_service.build(parsed, str(book_path))

        sections: list[Section] = []
        for chapter in skeleton.chapters:
            sections.extend(await self._sectioning_service.find_sections(parsed, chapter))

        result = BookStructuredComponents(
            book=skeleton.book,
            parts=skeleton.parts,
            chapters=skeleton.chapters,
            sections=sections,
        )

        await self._persist(result)

        logger.info(
            "ingest done book_id=%s parts=%d chapters=%d sections=%d",
            result.book.id,
            len(result.parts),
            len(result.chapters),
            len(result.sections),
        )
        return result

    async def _persist(self, result: BookStructuredComponents) -> None:
        """Write the assembled book structure to Postgres in a single transaction.

        Intent: keep the Pydantic → ORM mapping and the session-management ceremony
        in one place so the rest of the codebase never touches SQLAlchemy directly.
        """
        async with self._db.session() as session:
            session.add(BookRow.from_pydantic(result.book))
            session.add_all(PartRow.from_pydantic(p) for p in result.parts)
            session.add_all(ChapterRow.from_pydantic(c) for c in result.chapters)
            session.add_all(SectionRow.from_pydantic(s) for s in result.sections)
            await session.commit()
