import logging
from typing import TypeVar

from pydantic import BaseModel

from src.deps.llm.client import LlmCallResult, LlmClient
from src.deps.postgres.repositories import LlmCacheRepository

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class CachingLlmClient:
    """LlmClient adapter that consults a DB-backed response cache before hitting the model.

    Intent: pay for an LLM call exactly once per (task, input) and replay the
    persisted response on every subsequent call. The cache key is provided by the
    caller (typically the LLM task, which folds in its class name + VERSION + an
    input hash), so cache invalidation is deterministic — bump the task's VERSION
    and the next run misses-then-stores fresh content.
    """

    def __init__(
        self, inner: LlmClient, cache_repo: LlmCacheRepository, enabled: bool = True
    ) -> None:
        self._inner = inner
        self._cache = cache_repo
        self._enabled = enabled

    async def call_structured(
        self,
        system: str,
        user: str,
        response_model: type[T],
        *,
        cache_key: str | None = None,
    ) -> LlmCallResult[T]:
        """Return the cached response for `cache_key` if present; otherwise call through
        to the inner client and persist the result.

        When `cache_key` is None or caching is disabled, this is a passthrough.
        """
        if self._enabled and cache_key:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                logger.info(
                    "llm_tasks cache HIT key=%s response_model=%s",
                    cache_key,
                    response_model.__name__,
                )
                return LlmCallResult(
                    output=response_model.model_validate_json(cached.response_json),
                    model=cached.model,
                    input_tokens=cached.input_tokens,
                    output_tokens=cached.output_tokens,
                )
            logger.info(
                "llm_tasks cache MISS key=%s response_model=%s", cache_key, response_model.__name__
            )

        result = await self._inner.call_structured(system, user, response_model)

        if self._enabled and cache_key:
            await self._cache.put(
                cache_key=cache_key,
                response_json=result.output.model_dump_json(),
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        return result
