import hashlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from src.deps.epub_ingest import EpubIngestReader
from src.workflows.ingest.context import IngestContext
from src.workflows.ingest.events import HashingFile, IngestEvent, ParsingEpub

logger = logging.getLogger(__name__)

_HASH_CHUNK = 64 * 1024


class ParseEpubStep:
    """First stage: read the EPUB into a ParsedEpub and compute its content hash.

    The hash drives both LLM-cache keys (so re-runs of unchanged books are free) and
    idempotency (so unchanged books skip re-ingest entirely).
    """

    name = "parse_epub"

    def __init__(self, epub_reader: EpubIngestReader) -> None:
        self._epub_reader = epub_reader

    async def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]:
        yield ParsingEpub()
        ctx.parsed = self._epub_reader.read(ctx.book_path)

        yield HashingFile()
        ctx.file_hash = self._hash_file(ctx.book_path)
        logger.info(
            "parsed epub path=%s file_hash=%s spine_items=%d",
            ctx.book_path,
            ctx.file_hash,
            len(ctx.parsed.spine),
        )

    @staticmethod
    def _hash_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(_HASH_CHUNK):
                hasher.update(chunk)
        return hasher.hexdigest()
