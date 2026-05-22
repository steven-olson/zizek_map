import logging
from dataclasses import dataclass, field
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.builder import ParserRejectedMarkup
from ebooklib import epub

logger = logging.getLogger(__name__)


@dataclass
class HeadingMatch:
    level: int
    text: str
    char_offset: int


@dataclass
class SpineItem:
    idref: str
    file_path: str
    plaintext: str
    headings: list[HeadingMatch]
    id_offsets: dict[str, int] = field(default_factory=dict)


@dataclass
class TocNode:
    label: str
    file_path: str | None
    fragment: str | None = None
    children: list["TocNode"] = field(default_factory=list)


@dataclass
class ParsedEpub:
    title: str
    author: str | None
    spine: list[SpineItem]
    toc: list[TocNode]


class EpubIngestReader:
    """Owner of steps 1 + 2 of book ingestion.

    Step 1: locate the source — `list_available_epubs(books_dir)` enumerates `.epub`
    files for the UI; the user picks one.
    Step 2: parse — `read(path)` produces a `ParsedEpub` with the book's metadata,
    its ordered spine of `SpineItem`s (plaintext, heading offsets, element-id offsets),
    and the `toc.ncx` tree as `TocNode`s with `(file_path, fragment)` preserved.

    This module makes NO decisions about what counts as a chapter, a part, or a
    section — those judgments belong to BookSkeletonService and ChapterSectioningService.
    """

    def read(self, epub_path: Path) -> ParsedEpub:
        """Parse a single EPUB file into a ParsedEpub.

        The intent is to surface everything downstream code needs to reason about the
        book's structure — its title/author, its ordered spine of plaintext+headings,
        and its TOC tree — without the caller ever having to know the EPUB format.
        """
        logger.info("reading epub path=%s", epub_path)
        book = epub.read_epub(str(epub_path))

        title = self._first_metadata(book, "DC", "title") or epub_path.stem
        author = self._first_metadata(book, "DC", "creator")

        spine = self._build_spine(book)
        toc = [self._build_toc_node(entry) for entry in book.toc]

        logger.info(
            "parsed epub title=%r author=%r spine_items=%d toc_top_level=%d",
            title,
            author,
            len(spine),
            len(toc),
        )
        return ParsedEpub(title=title, author=author, spine=spine, toc=toc)

    def list_available_epubs(self, books_dir: Path) -> list[Path]:
        """Enumerate `.epub` files in `books_dir`, sorted for a stable UI listing.

        Intent: give the file-picker screen a deterministic, top-level list it can
        render without doing its own filesystem walking.
        """
        if not books_dir.exists():
            return []
        return sorted(p for p in books_dir.iterdir() if p.is_file() and p.suffix.lower() == ".epub")

    @staticmethod
    def _first_metadata(book: epub.EpubBook, namespace: str, name: str) -> str | None:
        """Return the first metadata value for a Dublin-Core-style key, or None.

        Intent: a tiny shim around ebooklib's quirky metadata API so callers can ask
        for `title` / `creator` without dealing with the (value, attrs) tuple shape.
        """
        items = book.get_metadata(namespace, name)
        if not items:
            return None
        value, _attrs = items[0]
        return value or None

    def _build_spine(self, book: epub.EpubBook) -> list[SpineItem]:
        """Walk the EPUB spine in reading order and produce a SpineItem per document.

        Intent: convert ebooklib's spine — a sequence of (idref, linear) tuples — into
        plaintext-ready units that downstream code can slice by character offset.
        """
        items: list[SpineItem] = []
        for idref, _linear in book.spine:
            item = book.get_item_with_id(idref)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            raw = item.get_content()
            plaintext, headings, id_offsets = self._extract_plaintext_and_headings(raw)
            items.append(
                SpineItem(
                    idref=idref,
                    file_path=item.get_name(),
                    plaintext=plaintext,
                    headings=headings,
                    id_offsets=id_offsets,
                )
            )
        return items

    def _build_toc_node(self, entry: object) -> TocNode:
        """Recursively translate one ebooklib TOC entry into a TocNode subtree.

        Intent: hide ebooklib's mixed tuple/Link/Section shape behind a uniform tree
        so the breakdown service can traverse the TOC without isinstance checks.
        """
        if isinstance(entry, tuple):
            head, children = entry
            label = getattr(head, "title", str(head))
            href = getattr(head, "href", None)
            file_path, fragment = self._split_href(href)
            return TocNode(
                label=label,
                file_path=file_path,
                fragment=fragment,
                children=[self._build_toc_node(c) for c in children],
            )
        label = getattr(entry, "title", str(entry))
        href = getattr(entry, "href", None)
        file_path, fragment = self._split_href(href)
        return TocNode(label=label, file_path=file_path, fragment=fragment, children=[])

    @staticmethod
    def _split_href(href: str | None) -> tuple[str | None, str | None]:
        """Split a TOC href into `(file_path, fragment)`, either of which may be None.

        Intent: TOC entries may point to an in-document anchor (e.g. `08_Chapter1.xhtml#sec1`).
        The breakdown service needs both halves: the bare file_path to match against a
        spine item, and the fragment to look up an element-id char offset for sections.
        """
        if href is None:
            return None, None
        file_path, _, fragment = href.partition("#")
        return (file_path or None), (fragment or None)

    def _extract_plaintext_and_headings(
        self, xhtml_bytes: bytes
    ) -> tuple[str, list[HeadingMatch], dict[str, int]]:
        """Render an XHTML document to plaintext, recording heading offsets and id offsets.

        Intent: produce the single source of truth that addresses Section ranges by
        `(spine_file_path, char_start, char_end)`. Heading offsets define h2/h3-driven
        section boundaries; element-id offsets let TOC fragments (`#filepos92795`) be
        resolved deterministically to a starting char position.
        """
        try:
            soup = BeautifulSoup(xhtml_bytes, "lxml-xml")
        except ParserRejectedMarkup:
            soup = BeautifulSoup(xhtml_bytes, "lxml")
        body = soup.find("body") or soup
        extractor = _PlaintextExtractor()
        extractor.walk(body)
        return extractor.text(), extractor.headings, extractor.id_offsets


class _PlaintextExtractor:
    _BLOCK_TAGS = {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "div",
        "blockquote",
        "li",
        "section",
        "article",
    }
    _HEADING_TAGS = {"h1", "h2", "h3"}

    def __init__(self) -> None:
        """Initialize an empty plaintext buffer that pretends to already end in a
        paragraph break so the first emitted block doesn't get phantom leading newlines."""
        self._buf: list[str] = []
        self._len: int = 0
        self._trailing_newlines: int = 2
        self.headings: list[HeadingMatch] = []
        self.id_offsets: dict[str, int] = {}

    def walk(self, node: Tag) -> None:
        """Public entry point — walk the given root and accumulate plaintext + headings.

        Intent: keep callers ignorant of recursion details; they hand us a root tag and
        then read `.text()` / `.headings`.
        """
        self._walk(node)

    def text(self) -> str:
        """Return the accumulated plaintext, preserving the offsets used by `.headings`.

        Intent: NEVER strip — heading char_offsets are absolute into this string and a
        strip would silently shift them.
        """
        return "".join(self._buf)

    def _walk(self, node: object) -> None:
        """Recursive visitor that emits plaintext and records heading offsets.

        Intent: blocks get separated by `\\n\\n`, `<br>` becomes `\\n`, and h1/h2/h3
        offsets are captured at the position right where the heading text starts in
        the final plaintext — so a section that begins at a heading can be located
        deterministically.
        """
        if isinstance(node, NavigableString):
            self._append(str(node))
            return
        if not isinstance(node, Tag):
            return
        name = (node.name or "").lower()
        if name == "br":
            self._append("\n")
            return
        if name in self._BLOCK_TAGS:
            self._ensure_paragraph_break()
        element_id = node.get("id")
        if element_id:
            self.id_offsets.setdefault(element_id, self._len)
        if name in self._HEADING_TAGS:
            heading_text = " ".join(node.get_text("", strip=False).split())
            self.headings.append(
                HeadingMatch(
                    level=int(name[1]),
                    text=heading_text,
                    char_offset=self._len,
                )
            )
        for child in node.children:
            self._walk(child)
        if name in self._BLOCK_TAGS:
            self._ensure_paragraph_break()

    def _ensure_paragraph_break(self) -> None:
        """Top up the buffer with newlines until it ends in `\\n\\n` (or stays empty).

        Intent: idempotent paragraph separator — calling it multiple times in a row
        between sibling block tags does not pile up extra blank lines.
        """
        if self._len == 0:
            return
        needed = 2 - self._trailing_newlines
        if needed > 0:
            self._append("\n" * needed)

    def _append(self, s: str) -> None:
        """Append `s` to the buffer and update the trailing-newline counter.

        Intent: keep `_trailing_newlines` in sync as a cheap state variable so
        `_ensure_paragraph_break` doesn't have to re-scan the buffer suffix.
        """
        if not s:
            return
        self._buf.append(s)
        self._len += len(s)
        n = 0
        for ch in reversed(s):
            if ch == "\n":
                n += 1
            else:
                self._trailing_newlines = n
                return
        self._trailing_newlines += n
