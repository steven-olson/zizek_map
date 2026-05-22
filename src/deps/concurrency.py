import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BoundedConcurrentRunner:
    """Run at most `max_concurrency` awaitables at a time.

    Intent: a single reusable primitive that wraps `asyncio.gather` with a semaphore.
    Steps that fan out across many independent sub-tasks (e.g. per-chapter section
    resolution) hand the runner a list of coroutines and respect the configured
    provider rate limit without each step inventing its own throttle.
    """

    def __init__(self, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        self._sem = asyncio.Semaphore(max_concurrency)
        self._max_concurrency = max_concurrency

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    async def gather(self, coros: list[Awaitable[T]]) -> list[T]:
        """Run all `coros` with bounded parallelism, preserving input order in the result."""

        async def _wrapped(coro: Awaitable[T]) -> T:
            async with self._sem:
                return await coro

        return await asyncio.gather(*[_wrapped(c) for c in coros])
