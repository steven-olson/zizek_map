import logging
from collections.abc import AsyncIterator

from src.deps.postgres.repositories import BookComponentRepository
from src.workflows.ingest.context import IngestContext
from src.workflows.ingest.events import IngestEvent, ReingestingExisting, SkippedAlreadyIngested

logger = logging.getLogger(__name__)


class CheckIdempotencyStep:
    """Decide whether to skip, re-ingest, or proceed fresh based on prior runs.

    Three outcomes, all driven by file_path + file_hash:
      - No prior Book at this file_path → proceed, no event.
      - Prior Book matches our file_hash → skip and yield `SkippedAlreadyIngested`.
      - Prior Book exists but file_hash differs → mark for cascade-delete during
        Persist and yield `ReingestingExisting`.
    """

    name = "check_idempotency"

    def __init__(self, repo: BookComponentRepository) -> None:
        self._repo = repo

    async def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]:
        assert ctx.file_hash is not None, "ParseEpubStep must populate file_hash first"
        existing = await self._repo.find_by_file_path(str(ctx.book_path))
        if existing is None:
            return

        if existing.file_hash == ctx.file_hash:
            logger.info(
                "skipping already-ingested book file_path=%s book_id=%s",
                ctx.book_path,
                existing.id,
            )
            ctx.skipped = True
            yield SkippedAlreadyIngested(book_id=existing.id)
            return

        logger.info(
            "file_path=%s exists with different hash old=%s new=%s — will re-ingest",
            ctx.book_path,
            existing.file_hash,
            ctx.file_hash,
        )
        ctx.existing_book_id_to_replace = existing.id
        yield ReingestingExisting(book_id=existing.id)
