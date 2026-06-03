"""
Sync SQLAlchemy session helper for Celery tasks.

Celery tasks run in a synchronous context; even though most service-layer
code is `async`, the persistence boundary we control here is sync (we use
psycopg). Async services that need a DB get the data they need via the
sync session passed into them and remain isolated from the event loop.

The engine is module-level so worker processes share it (one engine per
process), but sessions are scoped per-task via the `sync_session()`
context manager.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _register_pgvector(dbapi_connection, _connection_record) -> None:
    """Register the pgvector type adapters on each new psycopg connection.

    The ORM columns use ``pgvector.sqlalchemy.Vector``, but the clustering code
    reads/writes vectors via raw ``text()`` SQL, which bypasses that type. Without
    this, psycopg returns a ``vector`` column as its text form ``"[0.1,0.2,...]"``
    — and ``list(...)`` over that string yields a per-row-variable list of single
    characters, crashing ``np.asarray`` with an "inhomogeneous shape" error.
    Registering the adapter makes reads return ndarrays and writes accept lists.
    """
    from pgvector.psycopg import register_vector

    register_vector(dbapi_connection)


def _get_engine() -> Engine:
    global _engine, _SessionFactory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        event.listen(_engine, "connect", _register_pgvector)
        _SessionFactory = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, future=True
        )
    return _engine


@contextmanager
def sync_session() -> Iterator[Session]:
    """Yield a sync Session bound to `database_url_sync` (psycopg driver).

    Commits on clean exit, rolls back on exception, always closes.
    """
    _get_engine()
    assert _SessionFactory is not None
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["sync_session"]
