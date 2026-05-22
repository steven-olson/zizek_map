from collections.abc import AsyncIterator
from typing import Protocol

from src.workflows.ingest.context import IngestContext
from src.workflows.ingest.events import IngestEvent


class Step(Protocol):
    """One stage of the ingest pipeline.

    Reads/writes the shared `IngestContext` and yields progress events as it works.
    Implementations are kept small and single-purpose — a Step does one thing and
    the pipeline runner is responsible for ordering and error wrapping.
    """

    name: str

    def execute(self, ctx: IngestContext) -> AsyncIterator[IngestEvent]: ...
