# Implementation Plan: Part 1 — Book Component Ingestion

This plan covers part 1 of the zizek_map_mvp project: ingesting a book from an EPUB file, breaking it down into its constituent components (book → optional parts → chapters → sections), and persisting those components to Postgres.

Part 2 (summarization of those components) is out of scope here.

## Architecture conventions (recap)

- **Deps** (`src/deps/`) — non-business-logic utilities/clients (epub parsing, Anthropic SDK wrapper, Postgres session manager). No business logic allowed here.
- **Services** (`src/services/`) — pure business logic. A service has one specific business goal. It does **not** persist, monitor, or yield progress.
- **Workflows** (`src/workflows/`) — coordinate services + non-business concerns (persistence, monitoring, progress reporting). Entrypoints call workflows, not services (ideal; we may break this rule when it makes sense).
- **Models** (`src/models/`) — Pydantic models for the domain / LLM-side; SQLAlchemy ORM rows under `src/models/db/`.
- **Entrypoints** (`src/textual/`) — Textual UI is our only entrypoint. Top-level `main.py` is a thin kickoff wrapper around it.

## Breakdown strategy: hybrid EPUB-first

The EPUB format already encodes the book's structure cleanly via `toc.ncx` (TOC tree) and the spine of XHTML files. Trying to ignore that and re-derive structure with pure LLM tends to rediscover what the EPUB already states — and any LLM answer that disagrees with the EPUB's own metadata is almost certainly *worse*.

So we let the EPUB structure be the ground truth where it exists, and use the LLM where it doesn't:

1. **Deterministic from EPUB**: spine order, chapter file paths, h3-based section boundaries within each chapter.
2. **LLM does**:
   - Classify each spine item as front-matter / part-divider / chapter / back-matter.
   - Clean chapter titles (drop publisher prefixes, normalize drop-cap-styled headings).
   - Link chapters to their parent part-divider when the book has parts.
   - Fallback section detection for chapters that have no `<h3>` headings.

The example *Absolute Recoil* epub has: 22 XHTML files in the spine, Parts I/II/III as their own files, Chapters and Interludes as their own files, sections marked deterministically by `<h3 class="h3">` inside each chapter file.

## Location addressing

Components are addressed by `spine_file_path` + `char_start`/`char_end` (character offsets into that spine item's extracted plaintext). This is deterministic and reproducible: in part 2 we re-extract plaintext the same way and slice. No page numbers.

## Parts are first-class but optional

Some books have parts; some don't. The model supports both:

- A separate `Part` table.
- `Chapter.parent_book_id` is always present.
- `Chapter.parent_part_id` is nullable — `None` when the book has no parts.

The breakdown service detects whether the book has parts based on the LLM's spine classification. If no spine item is classified as `part_divider`, no `Part` rows are created and every chapter has `parent_part_id = None`.

---

## Phase 0 — Project plumbing

- Confirm Python `>=3.12` in `pyproject.toml`; update description.
- Add runtime deps via `uv add`: `ebooklib`, `beautifulsoup4`, `lxml`, `anthropic`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `textual`, `pydantic-settings`, `python-dotenv`.
- Add dev deps via `uv add --dev`: `black`, `isort`.
- Create `.env.example`:
  ```
  ANTHROPIC_API_KEY=
  ANTHROPIC_MODEL=claude-opus-4-7
  DATABASE_URL=postgresql+asyncpg://zizek:zizek@postgres:5432/zizek
  BOOKS_DIR=/app/books
  ```
- Add `Makefile` (targets: `up`, `down`, `migrate`, `revision`, `run`, `fmt`, `psql`).
- `.gitignore` (`.env`, `__pycache__`, `.venv`, build artifacts).
- **Keep top-level `main.py`** as the kickoff entry point. It becomes a thin wrapper:
  ```python
  from src.textual.app import run

  if __name__ == "__main__":
      run()
  ```

## Phase 1 — Settings & deps

### `src/settings.py`

`Settings(BaseSettings)` with:
- `anthropic_api_key: SecretStr`
- `anthropic_model: str = "claude-opus-4-7"`
- `database_url: str`
- `books_dir: Path = Path("/app/books")`

Reads from env / `.env`. Exposed via an `lru_cache`d `get_settings()` function.

### `src/deps/epub_ingest.py` *(renamed from `epub_utils.py`)*

Pure parsing utility. No business logic.

- `HeadingMatch` (dataclass): `level: int`, `text: str`, `char_offset: int`.
- `SpineItem` (dataclass): `idref: str`, `file_path: str` (e.g. `"OEBPS/08_Chapter1.xhtml"`), `plaintext: str`, `headings: list[HeadingMatch]`.
- `TocNode` (dataclass): `label: str`, `file_path: str | None`, `children: list[TocNode]`.
- `ParsedEpub` (dataclass): `title: str`, `author: str | None`, `spine: list[SpineItem]`, `toc: list[TocNode]`.
- `EpubIngestReader` class:
  - `read(epub_path: Path) -> ParsedEpub`
  - `list_available_epubs(books_dir: Path) -> list[Path]`
  - private helpers:
    - `_extract_plaintext_and_headings(xhtml_bytes) -> tuple[str, list[HeadingMatch]]` — bs4+lxml; walks tree, builds plaintext while recording each `<h2>` / `<h3>` start offset.
    - `_parse_toc(book) -> list[TocNode]` — recurses ebooklib's TOC tuples.

### `src/deps/claude_client/__init__.py`

Async wrapper around `anthropic.AsyncAnthropic`. No domain knowledge.

- `ClaudeClient`:
  - `__init__(api_key: SecretStr, model: str)`
  - `async call_structured(system: str, user: str, response_model: type[T]) -> T` — uses Anthropic's tool-use pattern: defines one tool whose `input_schema` is `response_model.model_json_schema()`, forces `tool_choice` to it, parses the `tool_use` block's `input` back through `response_model.model_validate`. Generic over any Pydantic model.

### `src/deps/postgres/__init__.py`

Connection/session management only.

- `Database` class: holds an `AsyncEngine`, exposes `session()` as an `@asynccontextmanager` yielding `AsyncSession`. Built from `Settings.database_url`.
- `Base = declarative_base()` exported here for ORM models to inherit.

## Phase 2 — Models

### `src/models/text_components.py` (revised Pydantic)

```python
class Book(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    author: str | None = None
    file_path: str

class Part(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    order_index: int
    parent_book_id: str

class Chapter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    order_index: int
    spine_file_path: str
    char_start: int
    char_end: int
    parent_book_id: str
    parent_part_id: str | None = None

class Section(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    order_index: int
    spine_file_path: str
    char_start: int
    char_end: int
    parent_chapter_id: str

class BookStructuredComponents(BaseModel):
    book: Book
    parts: list[Part]   # empty when the book has no parts
    chapters: list[Chapter]
    sections: list[Section]
```

### `src/models/llm_responses.py`

Narrow Pydantic shapes used only as structured-output targets for LLM calls.

- `SpineItemClassification`: `file_path: str`, `role: Literal["front_matter", "part_divider", "chapter", "back_matter"]`, `clean_title: str | None`, `parent_part_file_path: str | None`.
- `SpineClassificationResponse`: `items: list[SpineItemClassification]`.
- `SectionBreak`: `title: str`, `first_sentence_excerpt: str` (used to locate the start in plaintext).
- `SectionsResponse`: `sections: list[SectionBreak]`.

### `src/models/db/` (SQLAlchemy 2 ORM rows)

One file per class, each inheriting `Base`:
- `book_row.py` — `BookRow` (`id` PK, `title`, `author`, `file_path` UNIQUE, `created_at`).
- `part_row.py` — `PartRow` (`id` PK, `title`, `order_index`, `parent_book_id` FK → books CASCADE, `created_at`).
- `chapter_row.py` — `ChapterRow` (`id` PK, `title`, `order_index`, `spine_file_path`, `char_start`, `char_end`, `parent_book_id` FK CASCADE, `parent_part_id` FK CASCADE nullable, `created_at`).
- `section_row.py` — `SectionRow` (`id` PK, `title`, `order_index`, `spine_file_path`, `char_start`, `char_end`, `parent_chapter_id` FK CASCADE, `created_at`).

Each gets a `from_pydantic(cls, m) -> Self` classmethod (trivial field copy, ≤ 10 lines).

## Phase 3 — Alembic setup

- `alembic init -t async migrations`.
- Wire `migrations/env.py` to read `DATABASE_URL` from `Settings` and use `Base.metadata` for autogenerate.
- Generate `0001_initial.py` creating `books`, `parts`, `chapters`, `sections` tables. Hand-review the autogenerate output; commit.

## Phase 4 — Service (business logic only)

### `src/services/book_component_breakdown_service.py`

`BookComponentBreakdownService`:

- `__init__(epub_reader: EpubIngestReader, claude_client: ClaudeClient)` — dependencies injected so the workflow controls lifecycle and tests can swap.
- `async classify_book(parsed: ParsedEpub, file_path: str) -> tuple[Book, list[Part], list[Chapter]]`:
  1. Call `ClaudeClient.call_structured` with the TOC tree, each spine item's file_path + first ~300 chars of plaintext, asking for a `SpineClassificationResponse` (role per item, clean titles, part-linkage).
  2. Synthesize:
     - `Book` from epub metadata.
     - One `Part` per `part_divider` item (in spine order).
     - One `Chapter` per `chapter` item, `parent_part_id` resolved from `parent_part_file_path`.
     - `char_start = 0`, `char_end = len(spine_item.plaintext)` for chapters.
- `async resolve_sections(parsed: ParsedEpub, chapters: list[Chapter]) -> list[Section]`:
  - For each chapter:
    - Collect the chapter's spine item's `<h3>` headings.
    - If `len(h3s) >= 1`: one `Section` per heading, `char_start = heading.offset`, `char_end =` next heading's offset (or end of plaintext). Title cleaned by collapsing the drop-cap styling (`<h3>K<small>ANT AVEC</small>...</h3>` → `"KANT AVEC ALTHUSSER"`).
    - If `len(h3s) == 0` and plaintext > ~4000 chars: fallback per-chapter LLM call for `SectionsResponse`; locate each section's `char_start` via `plaintext.find(first_sentence_excerpt)`.
    - If `len(h3s) == 0` and plaintext is short: emit a single section spanning the whole chapter.
- `async get_book_components(book_epub_path: Path) -> BookStructuredComponents` — thin convenience wrapper for non-UI callers; calls both methods.
- Private helpers: `_build_classification_prompt`, `_locate_section_offsets`, `_clean_heading_text`.
- **No Postgres references anywhere in this class.**

## Phase 5 — Workflow (coordinator)

### `src/workflows/book_component_breakdown_workflow.py`

`BookComponentBreakdownWorkflow`:

- `__init__(book_path: Path, service: BookComponentBreakdownService, epub_reader: EpubIngestReader, db: Database)`.
- Small `IngestEvent` discriminated union (`@dataclass`s in the same file):
  - `ParsingEpub`
  - `ClassifyingSpine`
  - `ResolvingSections(chapter_title: str)`
  - `Persisting(counts: dict[str, int])`
  - `Done(book_id: str)`
  - `Failed(error: str)`
- `async def run(self) -> AsyncIterator[IngestEvent]`:
  1. `yield ParsingEpub()`; `parsed = self._epub_reader.read(self._book_path)`.
  2. `yield ClassifyingSpine()`; `book, parts, chapters = await self._service.classify_book(parsed, str(self._book_path))`.
  3. For each chapter we yield `ResolvingSections(chapter.title)` around the resolve call (workflow does this in a tight loop so the UI can show what's happening).
  4. `sections = await self._service.resolve_sections(parsed, chapters)`.
  5. `yield Persisting({...})`; map Pydantic → ORM with `BookRow.from_pydantic(book)` etc., open `async with self._db.session() as session:`, `session.add_all(...)`, `await session.commit()`.
  6. `yield Done(book_id=book.id)`.
- Body wrapped in `try / except Exception as e: yield Failed(str(e)); raise`.

The workflow is the only thing the Textual UI calls.

## Phase 6 — Textual entrypoint

Rename `src/entrypoints/` → `src/textual/`.

### `src/textual/app.py`

`ZizekMapApp(textual.App)`:
- Header, footer, `ContentSwitcher` with three screens.
- Bindings: `f` (file picker), `i` (ingested-books browser), `q` (quit).
- Exports a `run()` function that:
  1. Builds the dependency graph: `get_settings()` → `EpubIngestReader()`, `ClaudeClient(...)`, `Database(...)`, `BookComponentBreakdownService(...)`.
  2. Passes them to `ZizekMapApp`.
  3. Calls `app.run()`.

### `src/textual/screens/file_picker.py`

`FilePickerScreen`:
- `DataTable` listing epubs under `Settings.books_dir`. Columns: `filename`, `size`, `status` (badge: "not ingested" / "ingested" — looked up by `file_path` against the `books` table at mount).
- `enter` on a row → push `IngestScreen(epub_path)`.

### `src/textual/screens/ingest.py`

`IngestScreen`:
- A `RichLog` that streams the workflow's `IngestEvent`s.
- On mount: `asyncio.create_task` running `async for event in workflow.run(): self._log_event(event)`.
- Footer shows final outcome (book_id + counts) when `Done` fires.

### `src/textual/screens/ingested_books.py`

`IngestedBooksScreen`:
- Left `DataTable`: books from Postgres (`SELECT id, title, author FROM books`).
- Right `Tree` widget: on row select, populate with the book's parts → chapters → sections.

### Top-level `main.py`

Thin kickoff wrapper:
```python
from src.textual.app import run

if __name__ == "__main__":
    run()
```

Runnable two ways:
- `python main.py` (or `uv run python main.py`) — works on host once env is set up.
- `python -m src.textual` — alternate, also works inside the container.

## Phase 7 — Docker

### `Dockerfile`

Multi-stage:
- Base: `python:3.12-slim`.
- Install `uv` (`pip install uv`).
- `WORKDIR /app`, copy `pyproject.toml` + `uv.lock`, `uv sync --frozen --no-dev` to install into `.venv`.
- Copy `src/`, `migrations/`, `alembic.ini`, `main.py`.
- Default `CMD ["uv", "run", "python", "main.py"]`. (TUI needs `-it` from `docker compose run`.)

### `docker-compose.yml`

- `postgres`: `postgres:16-alpine`, env `POSTGRES_USER/PASSWORD/DB=zizek`, named volume `pgdata`, healthcheck, port `5432` exposed for host `psql` access.
- `app`: builds from `Dockerfile`, `depends_on: postgres (condition: service_healthy)`, env from `.env`, mounts `./books:/app/books:ro`, `tty: true`, `stdin_open: true`. Command can be overridden for migrations.

### `Makefile`

- `up`: `docker compose up -d postgres`
- `down`: `docker compose down`
- `migrate`: `docker compose run --rm app uv run alembic upgrade head`
- `revision`: `docker compose run --rm app uv run alembic revision --autogenerate -m "$(msg)"`
- `run`: `docker compose run --rm app`
- `fmt`: `uv run black . && uv run isort .`
- `psql`: `docker compose exec postgres psql -U zizek -d zizek`

## Phase 8 — Smoke test

End-to-end against the *Absolute Recoil* epub already in `books/`:

1. `make up && make migrate`.
2. `make run`, pick the file in the file picker, watch progress.
3. Verify in `make psql`:
   - 1 row in `books`.
   - 3 rows in `parts` (Part I / II / III).
   - 11 rows in `chapters` (9 numbered chapters + 2 interludes — confirm the LLM correctly treats interludes as chapter-equivalents).
   - h3-derived rows in `sections` (Chapter 1 should yield 3 sections — *Kant avec Althusser*, *The Forced Choice of Freedom*, *The Anticipatory Subject*).
4. Re-open the Textual app, navigate to the Ingested Books screen, drill into the tree, eyeball that the structure mirrors the book's TOC.

## Known follow-ups (out of scope here)

- **Idempotency**: re-ingesting the same epub currently inserts duplicates. Plan to add `UNIQUE (file_path)` on `books` and either `ON CONFLICT DO NOTHING` or delete-then-insert inside the workflow's transaction. Decide after happy path works.
- **LLM response caching** per file hash to avoid paying every dev run.
- **Test suite** — none planned for this phase; verification is via the UI + `make psql`.
