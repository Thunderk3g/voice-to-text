"""
Async + sync DB session plumbing.

- `engine` / `async_session_maker` — module-level singletons built from settings.
- `get_session()` — FastAPI dependency yielding AsyncSession.
- `get_sync_session()` — context manager for Celery / Alembic / scripts.
- `dispose_engine()` — engine teardown helper for graceful shutdown.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

# ---- Async (API + async workers) --------------------------------------------
engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

async_session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields an AsyncSession with automatic close."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Tear down the async engine pool (called on FastAPI shutdown)."""
    await engine.dispose()


# ---- Sync (Celery workers, Alembic, scripts) --------------------------------
sync_engine = create_engine(
    _settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

sync_session_maker: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
    autoflush=False,
    class_=Session,
)


@contextmanager
def get_sync_session() -> Iterator[Session]:
    """Synchronous session context manager for Celery worker code."""
    session = sync_session_maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_sync_engine() -> None:
    """Tear down the sync engine pool (called when a worker exits)."""
    sync_engine.dispose()
