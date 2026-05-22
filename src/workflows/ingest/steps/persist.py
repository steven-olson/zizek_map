import logging
from collections.abc import AsyncIterator

from src.deps.postgres.repositories import BookComponentRepository
from src.workflows.ingest.context import IngestContext
from src.workflows.ingest.events import IngestEvent, Persisting

logger = logging.getLogger(__name__)


class PersistStep:
    """Final stage: write the breakdown to Postgres.

    When `ctx.existing_book_id_to_replace` is set (the same file at a different hash),
    delete the prior Book first — its parts/chapters/sections are removed by FK
    cascade — then insert the fresh tree. Otherwise just insert.
    """

    name = "persist"

    def __init__(self, repo: BookComponentRepository) -> None:
        self._repo = repo

    async def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]:
        assert ctx.book is not None
        if ctx.existing_book_id_to_replace:
            logger.info(
                "replacing prior ingest book_id=%s with new book_id=%s",
                ctx.existing_book_id_to_replace,
                ctx.book.id,
            )
            await self._repo.delete_cascade(ctx.existing_book_id_to_replace)
        yield Persisting(counts=ctx.counts())
        await self._repo.save_breakdown(
            book=ctx.book,
            parts=ctx.parts,
            chapters=ctx.chapters,
            sections=ctx.sections,
        )
