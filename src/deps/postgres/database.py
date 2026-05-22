import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Database:
    """Connection / session management. No business logic."""

    def __init__(self, database_url: str, echo: bool = False) -> None:
        """Build the async engine + session factory bound to `database_url`.

        Intent: own connection-pool setup once at startup so workflows/screens never
        construct engines or sessionmakers themselves — they go through `.session()`.
        """
        self._engine: AsyncEngine = create_async_engine(database_url, echo=echo)
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield an `AsyncSession` and guarantee it gets closed when the block exits.

        Intent: callers `async with table_rows.session() as s:` and never have to remember to
        close — commits/rollbacks remain the caller's responsibility.
        """
        session = self._sessionmaker()
        try:
            yield session
        finally:
            await session.close()

    async def dispose(self) -> None:
        """Tear down the engine's connection pool on graceful shutdown.

        Intent: optional cleanup hook for tests and one-shot scripts; the long-running
        Textual app process simply exits and lets the OS reclaim sockets.
        """
        logger.info("disposing postgres engine")
        await self._engine.dispose()
