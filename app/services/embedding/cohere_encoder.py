"""
Cohere embed-multilingual-v3.0 encoder.

Implements the same _encode_sync contract that SentenceTransformer satisfies
so EmbeddingService can swap providers via the `embedding_provider` setting
without touching cache, prefix, or batching logic.

Returns L2-normalized float32 vectors of shape (N, 1024).
"""

from __future__ import annotations

import httpx
import numpy as np
import structlog

from app.core.config import get_settings
from app.utils.vector import l2_normalize

logger = structlog.get_logger(__name__)


class CohereEmbeddingError(RuntimeError):
    pass


class CohereEncoder:
    """Thin sync HTTP client around Cohere v2 embeddings."""

    def __init__(self) -> None:
        s = get_settings()
        if not s.cohere_api_key.get_secret_value():
            raise CohereEmbeddingError(
                "COHERE_API_KEY is empty; set it in .env to use provider=cohere."
            )
        self._base_url = s.cohere_base_url.rstrip("/")
        self._model = s.cohere_embed_model
        self._batch = int(s.cohere_batch_size)
        self._timeout = float(s.cohere_request_timeout_s)
        self._headers = {
            "Authorization": f"Bearer {s.cohere_api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def encode(self, texts: list[str], *, role: str = "passage") -> np.ndarray:
        """Embed a batch of texts. Returns (N, 1024) float32 L2-normalized.

        Cohere uses input_type="search_query" or "search_document" instead of
        the e5 query:/passage: prefix. We map our internal role accordingly and
        strip the e5 prefix if a caller has already added one.
        """
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)

        input_type = "search_query" if role == "query" else "search_document"
        cleaned = [_strip_e5_prefix(t) for t in texts]

        out: list[list[float]] = []
        with httpx.Client(timeout=self._timeout) as client:
            for start in range(0, len(cleaned), self._batch):
                batch = cleaned[start : start + self._batch]
                resp = client.post(
                    f"{self._base_url}/embed",
                    headers=self._headers,
                    json={
                        "model": self._model,
                        "texts": batch,
                        "input_type": input_type,
                        "embedding_types": ["float"],
                        "truncate": "END",
                    },
                )
                if resp.status_code >= 400:
                    logger.error(
                        "cohere_embed_failed",
                        status=resp.status_code,
                        body=resp.text[:400],
                    )
                    raise CohereEmbeddingError(
                        f"Cohere /embed returned {resp.status_code}: {resp.text[:200]}"
                    )

                data = resp.json()
                # v2 shape: {"embeddings": {"float": [[...], ...]}, ...}
                emb_block = data.get("embeddings") or {}
                vectors = emb_block.get("float") if isinstance(emb_block, dict) else None
                if vectors is None:
                    # tolerate the v1-style flat list
                    vectors = data.get("embeddings")
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise CohereEmbeddingError(
                        f"unexpected Cohere response shape: keys={list(data.keys())}"
                    )
                out.extend(vectors)

        arr = np.asarray(out, dtype=np.float32)
        return l2_normalize(arr, axis=1).astype(np.float32, copy=False)


def _strip_e5_prefix(text: str) -> str:
    """Remove an e5 'query: ' or 'passage: ' prefix if present."""
    if text.startswith("query: "):
        return text[len("query: ") :]
    if text.startswith("passage: "):
        return text[len("passage: ") :]
    return text


__all__ = ["CohereEncoder", "CohereEmbeddingError"]
