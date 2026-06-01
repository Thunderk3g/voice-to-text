"""
Multilingual embedding service with pluggable providers.

Providers:
  * ``local``  — SentenceTransformer(intfloat/multilingual-e5-large). Needs the
                 model on disk and optionally a GPU. Default historically.
  * ``cohere`` — Cohere embed-multilingual-v3.0 (hosted, 1024-dim). No torch
                 dependency at runtime. Selected when
                 ``settings.embedding_provider == "cohere"``.

The provider is loaded lazily on first use; importing this module is cheap.
All providers must return L2-normalized float32 vectors of shape (N, 1024).
The cache, batching, and e5 ``query:`` / ``passage:`` prefixing logic is
provider-agnostic so swapping providers does not invalidate behavior.
"""

from __future__ import annotations

import asyncio

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import embedding_cache_events, embeddings_generated
from app.models.schemas import EmbeddingRecord, ExtractedQuestion
from app.utils.lang import add_e5_prefix

logger = get_logger(__name__)


class EmbeddingService:
    """Generate L2-normalized 1024-dim multilingual embeddings.

    The underlying encoder is loaded lazily on first use to keep import-time
    cost negligible (and to avoid importing torch at module top when running
    against the hosted Cohere provider).
    """

    def __init__(self, cache: object | None = None) -> None:
        self._settings = get_settings()
        self._encoder = None  # local: SentenceTransformer | cohere: CohereEncoder
        self._lock = asyncio.Lock()
        self._cache = cache  # optional EmbeddingCache

    async def _ensure_encoder(self):
        if self._encoder is not None:
            return self._encoder
        async with self._lock:
            if self._encoder is None:
                provider = self._settings.embedding_provider
                if provider == "cohere":
                    # Local import — keeps httpx-only path clean of torch.
                    from app.services.embedding.cohere_encoder import CohereEncoder

                    logger.info(
                        "loading_embedding_encoder",
                        provider="cohere",
                        model=self._settings.cohere_embed_model,
                    )
                    self._encoder = CohereEncoder()
                else:
                    # Local import — torch must not be pulled at module top.
                    from sentence_transformers import SentenceTransformer

                    logger.info(
                        "loading_embedding_encoder",
                        provider="local",
                        model=self._settings.embedding_model,
                        device=self._settings.embedding_device,
                    )
                    model = await asyncio.to_thread(
                        SentenceTransformer,
                        self._settings.embedding_model,
                        device=self._settings.embedding_device,
                    )
                    try:
                        model.max_seq_length = self._settings.embedding_max_seq_len
                    except Exception:  # noqa: BLE001
                        pass
                    self._encoder = model
        return self._encoder

    # ------------------------------------------------------------------
    async def embed(
        self,
        texts: list[str],
        *,
        role: str = "passage",
    ) -> list[list[float]]:
        """Embed a list of texts. Each text is prefixed with 'query:' /
        'passage:' per e5 spec. Returns L2-normalized vectors of length
        ``settings.embedding_dim``.
        """
        if not texts:
            return []

        prefixed = [add_e5_prefix(t or "", role=role) for t in texts]

        # ---- Optional cache lookup ----
        cached_map: dict[str, list[float]] = {}
        if self._cache is not None:
            try:
                cached_map = await self._cache.get_many(prefixed)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                logger.warning("embedding_cache_get_failed", error=str(exc))
                embedding_cache_events.labels(event="error").inc()
                cached_map = {}

        # Cache metrics — count one event per requested text.
        if self._cache is not None:
            hits = sum(1 for t in prefixed if t in cached_map)
            embedding_cache_events.labels(event="hit").inc(hits)
            embedding_cache_events.labels(event="miss").inc(len(prefixed) - hits)

        missing_idx = [i for i, t in enumerate(prefixed) if t not in cached_map]
        missing_texts = [prefixed[i] for i in missing_idx]

        new_vectors: np.ndarray | None = None
        if missing_texts:
            encoder = await self._ensure_encoder()
            new_vectors = await asyncio.to_thread(
                self._encode_sync,
                encoder,
                missing_texts,
                role,
            )
            embeddings_generated.inc(len(missing_texts))

            if self._cache is not None and new_vectors is not None:
                try:
                    mapping = {
                        missing_texts[j]: new_vectors[j].tolist()
                        for j in range(len(missing_texts))
                    }
                    await self._cache.set_many(mapping)  # type: ignore[attr-defined]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("embedding_cache_set_failed", error=str(exc))

        # ---- Stitch cached + freshly computed back together ----
        dim = self._settings.embedding_dim
        out = np.zeros((len(prefixed), dim), dtype=np.float32)
        if cached_map:
            for i, t in enumerate(prefixed):
                if t in cached_map:
                    out[i] = np.asarray(cached_map[t], dtype=np.float32)
        if new_vectors is not None:
            out[np.asarray(missing_idx, dtype=np.int64)] = new_vectors

        return out.tolist()

    def _encode_sync(self, encoder, texts: list[str], role: str) -> np.ndarray:
        """Run the underlying encoder synchronously inside a thread."""
        # CohereEncoder: object exposes .encode(texts, role=...) and handles
        # batching + normalization itself.
        if self._settings.embedding_provider == "cohere":
            return encoder.encode(texts, role=role)

        # SentenceTransformer path.
        vecs = encoder.encode(
            texts,
            batch_size=self._settings.embedding_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vecs, dtype=np.float32)

    # ------------------------------------------------------------------
    async def embed_questions(
        self,
        questions: list[ExtractedQuestion],
    ) -> list[EmbeddingRecord]:
        """Embed a batch of extracted questions and return EmbeddingRecords."""
        if not questions:
            return []

        texts = [self._compose_text(q) for q in questions]
        vectors = await self.embed(texts, role="passage")

        model_name = (
            self._settings.cohere_embed_model
            if self._settings.embedding_provider == "cohere"
            else self._settings.embedding_model
        )
        dim = self._settings.embedding_dim

        records: list[EmbeddingRecord] = []
        for q, vec in zip(questions, vectors):
            if q.id is None:
                logger.warning("embedding_question_missing_id", call_id=str(q.call_id))
                continue
            records.append(
                EmbeddingRecord(
                    question_id=q.id,
                    model=model_name,
                    dim=dim,
                    vector=vec,
                )
            )
        return records

    @staticmethod
    def _compose_text(q: ExtractedQuestion) -> str:
        """normalized_text + ' || ' + english_gloss when gloss exists."""
        base = q.normalized_text or q.raw_text or ""
        if q.english_gloss:
            return f"{base} || {q.english_gloss}"
        return base


__all__ = ["EmbeddingService"]
