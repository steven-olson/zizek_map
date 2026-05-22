from dataclasses import dataclass

from src.deps.concurrency import BoundedConcurrentRunner
from src.deps.epub_ingest import EpubIngestReader
from src.deps.llm.caching_client import CachingLlmClient
from src.deps.llm.client import LlmClient
from src.deps.postgres.database import Database
from src.deps.postgres.repositories import BookComponentRepository, LlmCacheRepository
from src.workflows.ingest.pipeline import IngestPipeline
from src.workflows.ingest.steps import Step
from src.workflows.ingest.steps.check_idempotency import CheckIdempotencyStep
from src.workflows.ingest.steps.classify_spine import ClassifySpineStep
from src.workflows.ingest.steps.parse_epub import ParseEpubStep
from src.workflows.ingest.steps.persist import PersistStep
from src.workflows.ingest.steps.resolve_sections import ResolveSectionsStep
from src.observability import configure_observability
from src.settings import Settings, get_settings

_SERVICE_NAME = "zizek-map-mvp"

_SECTION_FALLBACK_THRESHOLD = 4000


@dataclass(frozen=True)
class AppDeps:
    """Long-lived dependencies shared by every entrypoint (Textual, CLI).

    Intent: the composition root builds this once; the UI and CLI see a single object
    with everything they need and never construct clients/sessions themselves.
    """

    settings: Settings
    epub_reader: EpubIngestReader
    db: Database
    book_repo: BookComponentRepository
    llm_client: CachingLlmClient
    concurrency: BoundedConcurrentRunner


def build_deps() -> AppDeps:
    """Construct the full dependency graph from environment-loaded settings.

    Intent: the only place in the codebase that wires clients and repositories
    together — add a new dependency once here and every entrypoint inherits it.
    """
    settings = get_settings()
    configure_observability(service_name=_SERVICE_NAME, log_level=settings.app.log_level)
    epub_reader = EpubIngestReader()
    db = Database(database_url=settings.database.url, echo=settings.database.echo)
    book_repo = BookComponentRepository(db=db)
    cache_repo = LlmCacheRepository(db=db)
    raw_llm = LlmClient(
        model=settings.llm.model,
        api_key=settings.llm.api_key,
        max_tokens=settings.llm.max_tokens,
    )
    llm_client = CachingLlmClient(
        inner=raw_llm, cache_repo=cache_repo, enabled=settings.llm.cache_enabled
    )
    concurrency = BoundedConcurrentRunner(max_concurrency=settings.llm.max_concurrent_calls)
    return AppDeps(
        settings=settings,
        epub_reader=epub_reader,
        db=db,
        book_repo=book_repo,
        llm_client=llm_client,
        concurrency=concurrency,
    )


def build_ingest_pipeline(deps: AppDeps) -> IngestPipeline:
    """Assemble the ingest pipeline. Same instance is reusable across many books."""
    steps: list[Step] = [
        ParseEpubStep(epub_reader=deps.epub_reader),
        CheckIdempotencyStep(repo=deps.book_repo),
        ClassifySpineStep(llm_client=deps.llm_client),
        ResolveSectionsStep(
            llm_client=deps.llm_client,
            runner=deps.concurrency,
            plaintext_threshold=_SECTION_FALLBACK_THRESHOLD,
        ),
        PersistStep(repo=deps.book_repo),
    ]
    return IngestPipeline(steps=steps)
