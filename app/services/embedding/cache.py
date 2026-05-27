"""
Redis-backed embedding cache.

Keys:   emb:{sha1(text)}:{model}
TTL:    7 days
Values: JSON-encoded list[float]

The cache is *tolerant* of Redis being unavailable: on any error we log and
fall back to a no-op so the embedding pipeline still completes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Async Redis cache for e5 embeddings."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        model: str | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        settings = get_settings()
        self._url = redis_url or settings.redis_url
        self._model = model or settings.embedding_model
        self._ttl = ttl_seconds
        self._client = None
        self._disabled = False

    @property
    def model(self) -> str:
        return self._model

    def _key(self, text: str) -> str:
        return f"emb:{_hash_text(text)}:{self._model}"

    async def _get_client(self):
        if self._disabled:
            return None
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as redis_async  # lazy import

            self._client = redis_async.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
            )
            return self._client
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_cache_init_failed", error=str(exc))
            self._disabled = True
            return None

    # ------------------------------------------------------------------
    async def get_many(self, texts: Iterable[str]) -> dict[str, list[float]]:
        """Return {text: vector} for cache hits. Misses are absent."""
        texts_list = list(texts)
        if not texts_list:
            return {}

        client = await self._get_client()
        if client is None:
            return {}

        keys = [self._key(t) for t in texts_list]
        try:
            raw = await client.mget(keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_cache_mget_failed", error=str(exc))
            return {}

        out: dict[str, list[float]] = {}
        for text, blob in zip(texts_list, raw):
            if not blob:
                continue
            try:
                out[text] = json.loads(blob)
            except (TypeError, ValueError):
                continue
        return out

    async def set_many(self, mapping: dict[str, list[float]]) -> None:
        """Bulk-set with TTL. Best-effort; failures are swallowed."""
        if not mapping:
            return
        client = await self._get_client()
        if client is None:
            return

        try:
            pipe = client.pipeline(transaction=False)
            for text, vec in mapping.items():
                pipe.set(self._key(text), json.dumps(vec), ex=self._ttl)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding_cache_set_failed", error=str(exc))

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None


__all__ = ["EmbeddingCache"]
