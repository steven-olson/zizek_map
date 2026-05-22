from dataclasses import dataclass


@dataclass(frozen=True)
class ParsingEpub:
    pass


@dataclass(frozen=True)
class HashingFile:
    pass


@dataclass(frozen=True)
class ReingestingExisting:
    book_id: str


@dataclass(frozen=True)
class SkippedAlreadyIngested:
    book_id: str


@dataclass(frozen=True)
class ClassifyingSpine:
    pass


@dataclass(frozen=True)
class ChapterStarted:
    chapter_title: str
    index: int
    total: int


@dataclass(frozen=True)
class ChapterCompleted:
    chapter_title: str
    section_count: int


@dataclass(frozen=True)
class Persisting:
    counts: dict[str, int]


@dataclass(frozen=True)
class Done:
    book_id: str
    counts: dict[str, int]


@dataclass(frozen=True)
class Failed:
    error: str


IngestEvent = (
    ParsingEpub
    | HashingFile
    | ReingestingExisting
    | SkippedAlreadyIngested
    | ClassifyingSpine
    | ChapterStarted
    | ChapterCompleted
    | Persisting
    | Done
    | Failed
)
