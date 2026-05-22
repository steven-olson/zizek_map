from dataclasses import dataclass, field
from pathlib import Path

from src.deps.epub_ingest import ParsedEpub
from src.deps.postgres.tables import Book, Chapter, Part, Section


@dataclass
class IngestContext:
    """Mutable accumulator threaded through the ingest pipeline's steps.

    Intent: each step reads what previous steps put on the context and writes its own
    output, so a step's signature stays simple (`async def execute(ctx, ...)`) without
    a growing list of arguments. Optional fields are set incrementally as the pipeline
    progresses.
    """

    book_path: Path
    file_hash: str | None = None
    parsed: ParsedEpub | None = None
    book: Book | None = None
    parts: list[Part] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    existing_book_id_to_replace: str | None = None
    skipped: bool = False

    def counts(self) -> dict[str, int]:
        return {
            "parts": len(self.parts),
            "chapters": len(self.chapters),
            "sections": len(self.sections),
        }
