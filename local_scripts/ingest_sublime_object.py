"""End-to-end ingest of *The Sublime Object of Ideology* — parses, classifies, sections, persists.

Run from the project root with:
    uv run python local_scripts/ingest_sublime_object.py

Re-runs are safe: any prior `books` row for this file is deleted first (cascades
to parts/chapters/sections via FK CASCADE) before the workflow runs. On failure,
the full traceback is printed and the script exits non-zero.

Requires `.env` to provide `LLM_API_KEY` and `DATABASE_URL`. The database must
already be migrated (`make migrate`).
"""

import asyncio
import sys
import traceback
from pathlib import Path

from sqlalchemy import delete

from src.deps.epub_ingest import EpubIngestReader
from src.deps.llm.llm_client import LlmClient
from src.deps.postgres.database import Database
from src.deps.postgres.table_rows import BookRow
from src.deps.telemetry import Telemetry
from src.services.book_skeleton_service import BookSkeletonService
from src.services.chapter_sectioning_service import ChapterSectioningService
from src.settings import get_settings
from src.workflows.book_ingest_workflow import BookIngestWorkflow

BOOK_GLOB = "The Sublime Object*.epub"

Telemetry.setup()


async def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    books_dir = project_root / "books"
    matches = sorted(books_dir.glob(BOOK_GLOB))
    if not matches:
        print(f"ERROR: no epub matching {BOOK_GLOB!r} in {books_dir}")
        return 1
    epub_path = matches[0]
    print(f"=> target: {epub_path.name}")

    settings = get_settings()
    epub_reader = EpubIngestReader()
    llm_client = LlmClient(model=settings.llm_model, api_key=settings.llm_api_key)
    db = Database(database_url=settings.database_url)
    workflow = BookIngestWorkflow(
        epub_reader=epub_reader,
        skeleton_service=BookSkeletonService(llm_client=llm_client),
        sectioning_service=ChapterSectioningService(llm_client=llm_client),
        db=db,
    )

    try:
        async with db.session() as session:
            cleared = await session.execute(
                delete(BookRow).where(BookRow.file_path == str(epub_path))
            )
            await session.commit()
        if cleared.rowcount:
            print(f"=> cleared {cleared.rowcount} prior book row(s)")

        result = await workflow.run(epub_path)

        print()
        print("=" * 72)
        print("SUCCESS")
        print("=" * 72)
        print(f"  book_id  : {result.book.id}")
        print(f"  title    : {result.book.title!r}")
        print(f"  author   : {result.book.author!r}")
        print(f"  parts    : {len(result.parts)}")
        print(f"  chapters : {len(result.chapters)}")
        print(f"  sections : {len(result.sections)}")
        if result.parts:
            print("  parts preview:")
            for p in result.parts[:5]:
                print(f"    - {p.title!r}")
        print("  chapters preview:")
        for c in result.chapters[:5]:
            n_secs = sum(1 for s in result.sections if s.parent_chapter_id == c.id)
            print(f"    - {c.title!r} ({n_secs} sections)")
        return 0
    except Exception:
        print()
        print("=" * 72)
        print("FAILURE")
        print("=" * 72)
        traceback.print_exc()
        return 2
    finally:
        await db.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
