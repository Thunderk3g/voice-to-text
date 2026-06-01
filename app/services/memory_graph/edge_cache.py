"""
Tiny Redis cache for memory-graph edge labels.

The relation between two clusters is stable as long as their centroids and
canonical FAQs don't change — both shift only on batch recluster / feedback.
Caching the LLM verdict per (source_id, target_id) avoids paying Groq tokens
for every nightly rebuild.

Failure is silent — if Redis is down we just call the LLM again.
"""

from __future__ import annotations

import json
from typing import Final
from uuid import UUID

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_NAMESPACE: Final[str] = "v2t:edge"
_EDGE_TTL_S: Final[int] = 7 * 24 * 60 * 60  # 7 days


def _redis_client():
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(
        get_settings().redis_url, decode_responses=True
    )


def _key(src: UUID, dst: UUID) -> str:
    return f"{_NAMESPACE}:{src}:{dst}"


async def get(src: UUID, dst: UUID) -> dict | None:
    """Return cached LLM payload for (src,dst) or None."""
    client = _redis_client()
    try:
        raw = await client.get(_key(src, dst))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("edge_cache_get_failed", error=str(exc))
        return None
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


async def put(src: UUID, dst: UUID, payload: dict) -> None:
    """Store the LLM payload for 7 days."""
    client = _redis_client()
    try:
        await client.set(_key(src, dst), json.dumps(payload), ex=_EDGE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.debug("edge_cache_put_failed", error=str(exc))
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["get", "put"]
