import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.deps.postgres.database import Database
from src.deps.postgres.tables import LlmCacheEntry

logger = logging.getLogger(__name__)


class LlmCacheRepository:
    """Persistence facade for the LLM response cache.

    Intent: hide all SQLAlchemy details behind two intent-named methods (`get`, `put`)
    so the caching client never sees a session.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, cache_key: str) -> LlmCacheEntry | None:
        """Look up a cached response by its task-derived key, or return None."""
        async with self._db.session() as session:
            result = await session.execute(
                select(LlmCacheEntry).where(LlmCacheEntry.cache_key == cache_key)
            )
            return result.scalar_one_or_none()

    async def put(
        self,
        *,
        cache_key: str,
        response_json: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Insert or replace the cached response for `cache_key`.

        Uses Postgres' `ON CONFLICT DO UPDATE` so re-runs against the same key overwrite
        rather than failing — useful when a prompt version is bumped and the same key
        intentionally needs new content.
        """
        stmt = (
            insert(LlmCacheEntry)
            .values(
                cache_key=cache_key,
                response_json=response_json,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            .on_conflict_do_update(
                index_elements=[LlmCacheEntry.cache_key],
                set_={
                    "response_json": response_json,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        )
        async with self._db.session() as session:
            await session.execute(stmt)
            await session.commit()
