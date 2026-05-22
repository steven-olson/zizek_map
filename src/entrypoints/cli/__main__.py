import argparse
import asyncio
import sys
from pathlib import Path

from src.ingest.composition import build_deps, build_ingest_pipeline
from src.ingest import (
    ChapterCompleted,
    ChapterStarted,
    ClassifyingSpine,
    Done,
    Failed,
    HashingFile,
    IngestEvent,
    ParsingEpub,
    Persisting,
    ReingestingExisting,
    SkippedAlreadyIngested,
)


def _format(event: IngestEvent) -> str:
    """Render an IngestEvent into a single log line for stdout.

    Intent: a deliberately plain renderer for the CLI — the Textual UI has its own
    rich-markup renderer; both consume the same event stream.
    """
    match event:
        case ParsingEpub():
            return "Parsing EPUB..."
        case HashingFile():
            return "Hashing file..."
        case ReingestingExisting(book_id=bid):
            return (
                f"Existing book at this path with a different hash — replacing (old book_id={bid})"
            )
        case SkippedAlreadyIngested(book_id=bid):
            return f"Skipped: already ingested at this hash (book_id={bid})"
        case ClassifyingSpine():
            return "Classifying spine items..."
        case ChapterStarted(chapter_title=t, index=i, total=n):
            return f"  [{i + 1}/{n}] resolving: {t}"
        case ChapterCompleted(chapter_title=t, section_count=n):
            return f"  done: {t} ({n} sections)"
        case Persisting(counts=counts):
            return f"Persisting: {counts}"
        case Done(book_id=bid, counts=counts):
            return f"DONE book_id={bid} counts={counts}"
        case Failed(error=err):
            return f"FAILED: {err}"


async def _ingest(path: Path) -> int:
    deps = build_deps()
    pipeline = build_ingest_pipeline(deps)
    failed = False
    try:
        async for event in pipeline.run(path):
            print(_format(event), flush=True)
            if isinstance(event, Failed):
                failed = True
    finally:
        await deps.db.dispose()
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="src.cli", description="zizek_map_mvp CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest_p = sub.add_parser("ingest", help="ingest one EPUB into Postgres")
    ingest_p.add_argument("path", type=Path, help="path to the .epub file")
    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        return asyncio.run(_ingest(args.path))
    return 2


if __name__ == "__main__":
    sys.exit(main())
