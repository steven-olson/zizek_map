import logging

from src.deps.epub_ingest import ParsedEpub, SpineItem, TocNode
from src.deps.llm.llm_client import LlmClient
from src.deps.llm.prompts.spine_classification import SpineClassificationPrompt
from src.models.llm_responses import SpineClassificationResponse, SpineItemClassification
from src.models.text_components import Book, BookSkeleton, Chapter, Part

logger = logging.getLogger(__name__)


class BookSkeletonService:
    """Build a book's chapter/part skeleton from a parsed EPUB.

    Owns steps 3 + 4 of ingestion: deciding which spine items are real content (vs
    front/back matter), what structural role each plays (part_divider / chapter /
    front_matter / back_matter), and synthesizing the corresponding Book + Part[] +
    Chapter[] domain objects. Section-level structure within chapters is NOT this
    service's job — that belongs to ChapterSectioningService.
    """

    def __init__(self, llm_client: LlmClient) -> None:
        """Stash the LLM client used for the spine-classification call.

        Intent: dependency injection so the workflow controls the client lifecycle
        and tests can swap in a fake without monkey-patching.
        """
        self._llm_client = llm_client

    async def build(self, parsed: ParsedEpub, source_file_path: str) -> BookSkeleton:
        """Classify spine items and assemble the Book + Part[] + Chapter[] skeleton.

        Intent: one LLM round-trip drives all the structural decisions for the book.
        We first drop spine items not referenced anywhere in the TOC so the prompt
        stays tractable for Calibre-generated EPUBs (which can spill thousands of
        footnote files into the spine), then synthesize Pydantic objects in spine order.
        """
        spine_to_classify = self._filter_spine_via_toc(parsed)
        logger.info(
            "building skeleton: classifying %d of %d spine items for book=%r",
            len(spine_to_classify),
            len(parsed.spine),
            parsed.title,
        )

        classifications = await self._classify_spine(parsed, spine_to_classify)
        classifications_by_path = {item.file_path: item for item in classifications.items}

        book = Book(title=parsed.title, author=parsed.author, file_path=source_file_path)
        parts, part_id_by_file_path = self._build_parts(parsed, classifications_by_path, book.id)
        chapters = self._build_chapters(
            parsed, classifications_by_path, book.id, part_id_by_file_path
        )
        self._validate_chapter_part_links(parts, chapters)

        logger.info(
            "skeleton built: book=%s parts=%d chapters=%d",
            book.id,
            len(parts),
            len(chapters),
        )
        return BookSkeleton(book=book, parts=parts, chapters=chapters)

    @staticmethod
    def _validate_chapter_part_links(parts: list[Part], chapters: list[Chapter]) -> None:
        """Assert every chapter's `parent_part_id` resolves to an actual Part in this batch.

        Intent: this is an invariant of `_build_chapters` (it only sets parent_part_id
        via a lookup in `part_id_by_file_path`, whose values are the part ids we just
        created), so this check is paranoia. But if it ever fails, the FK violation
        surfaces at the service layer with a clear message — much easier to diagnose
        than a generic Postgres FK error during commit.
        """
        valid_part_ids = {p.id for p in parts}
        for chapter in chapters:
            if chapter.parent_part_id is None:
                continue
            if chapter.parent_part_id not in valid_part_ids:
                raise RuntimeError(
                    f"chapter {chapter.title!r} (spine={chapter.spine_file_path!r}) has "
                    f"parent_part_id={chapter.parent_part_id!r}, which is not in the set of "
                    f"built Part ids {sorted(valid_part_ids)!r} — this should never happen, "
                    "so _build_chapters has a bug or `parent_part_id` was mutated post-construction."
                )

    async def _classify_spine(
        self, parsed: ParsedEpub, spine_items: list[SpineItem]
    ) -> SpineClassificationResponse:
        """Run the spine-classification LLM call for the given (filtered) spine items.

        Intent: keep the prompt + response-model pairing in one place so the rest of
        the service stays focused on synthesis rather than transport.
        """
        return await self._llm_client.call_structured(
            system=SpineClassificationPrompt.SYSTEM,
            user=SpineClassificationPrompt.build_user(parsed, spine_items),
            response_model=SpineClassificationResponse,
        )

    def _filter_spine_via_toc(self, parsed: ParsedEpub) -> list[SpineItem]:
        """Return the subset of spine items worth sending to the classifier.

        Intent: Calibre-generated EPUBs often spill each footnote into its own spine
        document, ballooning the spine into the thousands. The TOC is authoritative
        about what counts as a structurally meaningful unit, so we keep only spine
        items whose file_path appears anywhere in the TOC tree. If the TOC is empty,
        we fall back to the full spine so books without a toc.ncx still work.
        """
        toc_file_paths = self._collect_toc_file_paths(parsed.toc)
        if not toc_file_paths:
            return parsed.spine
        return [item for item in parsed.spine if item.file_path in toc_file_paths]

    @staticmethod
    def _collect_toc_file_paths(toc: list[TocNode]) -> set[str]:
        """Walk the TOC tree and collect every distinct (bare) file_path referenced.

        Intent: the set of spine files that the book itself considers structural —
        anything not in here is almost certainly noise we can drop before classification.
        """
        paths: set[str] = set()
        stack: list[TocNode] = list(toc)
        while stack:
            node = stack.pop()
            if node.file_path is not None:
                paths.add(node.file_path)
            stack.extend(node.children)
        return paths

    @staticmethod
    def _build_parts(
        parsed: ParsedEpub,
        classifications_by_path: dict[str, SpineItemClassification],
        book_id: str,
    ) -> tuple[list[Part], dict[str, str]]:
        """Synthesize one Part per `part_divider` spine item, in spine order.

        Returns the list of parts and a map from each part's spine file_path to its
        generated id, so chapters can resolve their `parent_part_id` by file_path.
        """
        parts: list[Part] = []
        part_id_by_file_path: dict[str, str] = {}
        for spine_item in parsed.spine:
            cls = classifications_by_path.get(spine_item.file_path)
            if cls is None or cls.role != "part_divider":
                continue
            part = Part(
                title=(cls.clean_title or spine_item.file_path).strip(),
                order_index=len(parts),
                parent_book_id=book_id,
            )
            parts.append(part)
            part_id_by_file_path[spine_item.file_path] = part.id
        logger.info(
            "built %d parts: %s",
            len(parts),
            [(p.title, p.id) for p in parts],
        )
        return parts, part_id_by_file_path

    @staticmethod
    def _build_chapters(
        parsed: ParsedEpub,
        classifications_by_path: dict[str, SpineItemClassification],
        book_id: str,
        part_id_by_file_path: dict[str, str],
    ) -> list[Chapter]:
        """Synthesize one Chapter per `chapter` spine item, linked to its parent Part if any.

        Intent: char range is the full spine item (0..len); section boundaries within
        that range are resolved later by ChapterSectioningService.
        """
        chapters: list[Chapter] = []
        for spine_item in parsed.spine:
            cls = classifications_by_path.get(spine_item.file_path)
            if cls is None or cls.role != "chapter":
                continue
            parent_part_id: str | None = None
            if cls.parent_part_file_path:
                parent_part_id = part_id_by_file_path.get(cls.parent_part_file_path)
                if parent_part_id is None:
                    logger.warning(
                        "chapter spine=%r references parent_part_file_path=%r which has no "
                        "matching part_divider in this book; leaving parent_part_id=None",
                        spine_item.file_path,
                        cls.parent_part_file_path,
                    )
            chapters.append(
                Chapter(
                    title=(cls.clean_title or spine_item.file_path).strip(),
                    order_index=len(chapters),
                    spine_file_path=spine_item.file_path,
                    char_start=0,
                    char_end=len(spine_item.plaintext),
                    parent_book_id=book_id,
                    parent_part_id=parent_part_id,
                )
            )
        logger.info(
            "built %d chapters; parent_part_id distribution: %s",
            len(chapters),
            {
                pid: sum(1 for c in chapters if c.parent_part_id == pid)
                for pid in {c.parent_part_id for c in chapters}
            },
        )
        return chapters
