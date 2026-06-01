"""
Tiny idempotency store backed by Redis.

Callers pass an opaque ``Idempotency-Key`` header. We map (route, key) to the
resource id of the first successful response and replay it for any retry
inside ``IDEMPOTENCY_TTL_SECONDS`` (24h by default). This protects /ingest
against duplicate calls when an HTTP client retries.

The store is a thin helper, not a FastAPI dependency, so route code can keep
the lookup/write inline with the rest of its flow.
"""

from __future__ import annotations

from typing import Final

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

# Hash-tagged namespace so a future migration to Redis Cluster keeps everything
# in one slot.
_NAMESPACE: Final[str] = "v2t:idem"
IDEMPOTENCY_TTL_SECONDS: Final[int] = 24 * 60 * 60
_MAX_KEY_LEN: Final[int] = 128


def _redis_client():
    """Lazy Redis client. Imported inside so module load stays cheap."""
    import redis.asyncio as redis_asyncio

    settings = get_settings()
    return redis_asyncio.from_url(settings.redis_url, decode_responses=True)


def _normalize_key(key: str | None) -> str | None:
    if not key:
        return None
    key = key.strip()
    if not key or len(key) > _MAX_KEY_LEN:
        return None
    return key


async def lookup(route: str, key: str | None) -> str | None:
    """Return the stored resource id for (route, key) or None."""
    norm = _normalize_key(key)
    if norm is None:
        return None
    client = _redis_client()
    try:
        return await client.get(f"{_NAMESPACE}:{route}:{norm}")
    except Exception as exc:  # noqa: BLE001 — never fail the request on Redis hiccups
        logger.warning("idempotency_lookup_failed", error=str(exc))
        return None
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


async def remember(route: str, key: str | None, resource_id: str) -> None:
    """Persist (route, key) -> resource_id with a 24h TTL."""
    norm = _normalize_key(key)
    if norm is None:
        return
    client = _redis_client()
    try:
        await client.set(
            f"{_NAMESPACE}:{route}:{norm}",
            resource_id,
            ex=IDEMPOTENCY_TTL_SECONDS,
            nx=True,  # don't overwrite — first writer wins
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("idempotency_remember_failed", error=str(exc))
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["lookup", "remember", "IDEMPOTENCY_TTL_SECONDS"]
