"""
Liveness and readiness endpoints.

`/healthz` is a cheap "is the process up" probe. `/readyz` pings the
critical dependencies (Postgres + Redis) so K8s won't route traffic to
a process that can't actually serve requests yet.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import ORJSONResponse
from sqlalchemy import text

from app import __version__
from app.api.dependencies import get_db
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Always-on liveness probe."""

    settings = get_settings()
    return {"status": "ok", "env": settings.app_env, "version": __version__}


@router.get("/readyz")
async def readyz() -> ORJSONResponse:
    """Readiness probe: ping Postgres and Redis."""

    settings = get_settings()
    checks: dict[str, str] = {}
    overall_ok = True

    # Postgres
    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
            break
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        logger.warning("readyz_postgres_failed", error=str(exc))
        checks["postgres"] = f"error: {type(exc).__name__}"
        overall_ok = False

    # Redis
    try:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("readyz_redis_failed", error=str(exc))
        checks["redis"] = f"error: {type(exc).__name__}"
        overall_ok = False

    status_code = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return ORJSONResponse(
        status_code=status_code,
        content={"status": "ok" if overall_ok else "degraded", "checks": checks},
    )
