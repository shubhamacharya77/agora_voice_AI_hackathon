"""PostgreSQL connection and session handling (SQLModel on async SQLAlchemy over psycopg 3).

Sessions are SQLModel's AsyncSession, so queries use `await session.exec(select(...))`.
On Windows, psycopg's async mode needs the selector event loop, so the server is
started with `--loop asyncio:SelectorEventLoop` (see README).
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import Request
# SQLModel has no async engine or session factory of its own, so those two come from
# SQLAlchemy (the layer SQLModel is built on). Everything else is SQLModel.
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str, connect_timeout_seconds: int = 3) -> None:
        self._connect_timeout_seconds = connect_timeout_seconds
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": connect_timeout_seconds},
        )
        self.sessionmaker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def ping(self) -> bool:
        """Return True if a trivial query succeeds within the connect timeout."""
        try:
            async with asyncio.timeout(self._connect_timeout_seconds + 2):
                async with self.engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # health checks report failure; they never raise
            logger.warning("database ping failed: %s: %s", type(exc).__name__, exc)
            return False

    @property
    def dsn(self) -> str:
        """Plain libpq connection string (for LangGraph's Postgres checkpointer)."""
        return self.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)

    async def dispose(self) -> None:
        await self.engine.dispose()


def get_database(request: Request) -> Database:
    return request.app.state.db


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with get_database(request).sessionmaker() as session:
        yield session
