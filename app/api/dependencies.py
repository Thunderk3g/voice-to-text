"""
FastAPI dependency providers.

Heavy, stateful clients (embedding model, Groq HTTP client) are created
ONCE per process and reused across requests. The API process is async,
so threading concerns for these singletons are handled inside the
service classes themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover — import-time only
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.clustering.engine import ClusterEngine
    from app.services.embedding.e5 import EmbeddingService
    from app.services.memory_graph.builder import MemoryGraphBuilder

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------
async def get_db() -> AsyncIterator["AsyncSession"]:
    """Yield a SQLAlchemy AsyncSession.

    Re-exports :func:`app.db.session.get_session` so route modules only
    depend on this package.
    """

    from app.db.session import get_session

    async for session in get_session():
        yield session


# ---------------------------------------------------------------------------
# Heavyweight singletons
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_embedding_service() -> "EmbeddingService":
    """Process-wide multilingual-e5-large embedding service."""

    from app.services.factories import get_embedding_service as _factory

    logger.info("embedding_service_init")
    return _factory()


@lru_cache(maxsize=1)
def get_llm_client():  # type: ignore[no-untyped-def]
    """Process-wide Groq (OpenAI-compatible) HTTP client."""

    from app.services.factories import get_llm_client as _factory

    logger.info("groq_client_init")
    return _factory()


# ---------------------------------------------------------------------------
# Per-request wired services
# ---------------------------------------------------------------------------
async def get_cluster_engine(
    db: "AsyncSession" = Depends(get_db),  # noqa: ARG001 — kept for future async overrides
) -> "ClusterEngine":
    """Fully-wired ClusterEngine.

    Today the engine talks to Postgres through sync helpers wrapped in
    ``asyncio.to_thread``; the ``db`` parameter is accepted but unused so
    the dependency graph still flags route-level DB requirements.
    """
    from app.services.factories import make_cluster_engine

    return make_cluster_engine()


async def get_memory_graph_builder(
    db: "AsyncSession" = Depends(get_db),  # noqa: ARG001 — same reason
) -> "MemoryGraphBuilder":
    """Fully-wired MemoryGraphBuilder (Groq + DB-backed neighbor fetch)."""
    from app.services.factories import make_memory_graph_builder

    return make_memory_graph_builder()


# ---------------------------------------------------------------------------
# Optional API key auth (skipped in local env)
# ---------------------------------------------------------------------------
async def api_key_auth(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Header-based API key check, enforced in non-local environments."""

    settings = get_settings()
    if settings.app_env == "local":
        return

    expected = getattr(settings, "api_key", None)
    if expected is None:
        return

    expected_value = expected.get_secret_value() if hasattr(expected, "get_secret_value") else str(expected)
    if not x_api_key or x_api_key != expected_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
