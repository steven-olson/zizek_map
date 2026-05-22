import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from src.deps.epub_ingest import EpubIngestReader
from src.deps.postgres.database import Database
from src.deps.postgres.table_rows import BookRow
from src.deps.postgres.table_rows.chapter_row import ChapterRow
from src.deps.postgres.table_rows import PartRow
from src.deps.postgres.table_rows import SectionRow
from src.services.book_component_breakdown_service import BookComponentBreakdownService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsingEpub:
    pass


@dataclass(frozen=True)
class ClassifyingSpine:
    pass


@dataclass(frozen=True)
class ResolvingSections:
    chapter_title: str
    index: int
    total: int


@dataclass(frozen=True)
class Persisting:
    counts: dict[str, int]


@dataclass(frozen=True)
class Done:
    book_id: str
    counts: dict[str, int]


@dataclass(frozen=True)
class Failed:
    error: str


IngestEvent = ParsingEpub | ClassifyingSpine | ResolvingSections | Persisting | Done | Failed


class BookComponentBreakdownWorkflow:
    """Coordinator: runs the breakdown service, persists the result to Postgres,
    and yields progress events for the UI to consume."""

    def __init__(
        self,
        book_path: Path,
        service: BookComponentBreakdownService,
        epub_reader: EpubIngestReader,
        db: Database,
    ) -> None:
        """Stash the inputs and deps this workflow needs to run end-to-end.

        Intent: each `BookComponentBreakdownWorkflow` is a single-use coordinator
        scoped to one book — the file_picker creates one per ingest action.
        """
        self._book_path = book_path
        self._service = service
        self._epub_reader = epub_reader
        self._db = db

    async def run(self) -> AsyncIterator[IngestEvent]:
        """Drive the full ingest and yield progress events as it goes.

        Intent: the only public API of this class — the Textual screen consumes the
        event stream live (parse → classify → per-chapter section resolution →
        persist → done). All persistence happens inside one transaction so a failure
        leaves Postgres untouched, and a `Failed` event is always emitted before
        re-raising so the UI gets a final word.
        """
        logger.info("workflow start book_path=%s", self._book_path)
        try:
            yield ParsingEpub()
            parsed = self._epub_reader.read(self._book_path)

            yield ClassifyingSpine()
            book, parts, chapters = await self._service.classify_book(parsed, str(self._book_path))

            sections = []
            for idx, chapter in enumerate(chapters):
                yield ResolvingSections(chapter_title=chapter.title, index=idx, total=len(chapters))
                chapter_sections = await self._service.resolve_sections_for_chapter(parsed, chapter)
                sections.extend(chapter_sections)

            counts = {
                "parts": len(parts),
                "chapters": len(chapters),
                "sections": len(sections),
            }
            yield Persisting(counts=counts)

            async with self._db.session() as session:
                session.add(BookRow.from_pydantic(book))
                session.add_all(PartRow.from_pydantic(p) for p in parts)
                session.add_all(ChapterRow.from_pydantic(c) for c in chapters)
                session.add_all(SectionRow.from_pydantic(s) for s in sections)
                await session.commit()

            logger.info("workflow done book_id=%s counts=%s", book.id, counts)
            yield Done(book_id=book.id, counts=counts)
        except Exception as exc:
            logger.exception("workflow failed book_path=%s", self._book_path)
            yield Failed(error=f"{type(exc).__name__}: {exc}")
            raise
