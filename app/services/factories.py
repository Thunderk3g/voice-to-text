"""
Service factories — the integration glue between Celery workers and the
async clustering / LLM / canonicalization / memory-graph services.

The services were designed with injected async callables so they could be
swapped in tests. Production wiring lives here so workers can build a
fully-wired service with one call:

    extractor = make_llm_extractor()
    canonicalizer = make_faq_canonicalizer()
    memory_builder = make_memory_graph_builder()
    cluster_engine = make_cluster_engine()

Each factory wraps the synchronous DB helpers in `app.workers.cluster_glue`
in ``asyncio.to_thread`` so the async services can ``await`` them without
blocking the loop. A fresh ``sync_session`` is opened per call and closed
afterwards — keeps connection pressure low and avoids carrying transactional
state across the asyncio boundary.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import numpy as np

from app.clustering.engine import ClusterEngine
from app.clustering.incremental import IncrementalAssigner
from app.models.enums import EdgeRelation, Intent, Language
from app.models.schemas import CanonicalFAQ, ClusterRecord, ExtractedQuestion
from app.services.canonicalization.faq import (
    ClusterContext,
    ClusterExample,
    FAQCanonicalizer,
)
from app.services.embedding.e5 import EmbeddingService
from app.services.extraction.llm_extractor import LLMExtractor
from app.services.llm.ollama_client import OllamaClient
from app.services.memory_graph.builder import (
    ClusterSummary,
    MemoryGraphBuilder,
    NeighborHit,
)
from app.workers import cluster_glue as glue
from app.workers.db import sync_session


# ---------------------------------------------------------------------------
# Shared singletons. The OllamaClient is HTTP-only so cheap to keep around;
# the EmbeddingService loads ~2 GB of model so we very much want to share it.
# ---------------------------------------------------------------------------
_llm_client: OllamaClient | None = None
_embedding_service: EmbeddingService | None = None


def get_llm_client() -> OllamaClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = OllamaClient()
    return _llm_client


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


# ---------------------------------------------------------------------------
# LLM extractor — only needs the Ollama client.
# ---------------------------------------------------------------------------
def make_llm_extractor() -> LLMExtractor:
    return LLMExtractor(get_llm_client())


# ---------------------------------------------------------------------------
# FAQ canonicalizer — needs an async fetcher for ClusterContext.
# ---------------------------------------------------------------------------
async def _fetch_cluster_context_for_canon(cluster_id: UUID) -> ClusterContext:
    def _work() -> ClusterContext:
        with sync_session() as session:
            clusters = glue.list_clusters(session, only_stable=False)
            cluster = next(
                (c for c in clusters if str(c["id"]) == str(cluster_id)),
                None,
            )
            if cluster is None:
                raise LookupError(f"cluster {cluster_id} not found")
            examples_raw = glue.get_cluster_examples(session, cluster_id, limit=24)
            # Get embeddings for those question_ids.
            emb_rows = (
                session.execute_text  # placeholder; real impl uses fetch by ids
                if False
                else _fetch_embeddings_by_question_ids(
                    session, [r["id"] for r in examples_raw]
                )
            )
            embeddings = {row["question_id"]: list(row["vector"]) for row in emb_rows}
            examples = [
                ClusterExample(
                    question_id=r["id"],
                    text=r.get("normalized_text") or r.get("raw_text") or "",
                    embedding=embeddings.get(r["id"], []),
                )
                for r in examples_raw
                if embeddings.get(r["id"])
            ]
            centroid = _vec_to_list(cluster.get("centroid"))
            dominant_intents_raw = cluster.get("dominant_intents") or []
            dominant_intents = [
                Intent(i) if isinstance(i, str) and i in Intent._value2member_map_ else Intent.OTHER
                for i in dominant_intents_raw
            ]
            dominant_language = (
                Language(cluster["dominant_language"])
                if cluster.get("dominant_language")
                else Language.OTHER
            )
            return ClusterContext(
                cluster_id=cluster_id,
                centroid=centroid,
                dominant_language=dominant_language,
                dominant_intents=dominant_intents or [Intent.OTHER],
                total_members=cluster.get("frequency", len(examples)),
                examples=examples,
            )

    return await asyncio.to_thread(_work)


def _fetch_embeddings_by_question_ids(session, qids: list) -> list[dict[str, Any]]:
    """Helper: load embeddings for a list of question ids."""
    if not qids:
        return []
    from sqlalchemy import text as _sql

    rows = (
        session.execute(
            _sql(
                """
                SELECT question_id, vector
                FROM embeddings
                WHERE question_id = ANY(:ids)
                """
            ),
            {"ids": [str(q) for q in qids]},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def make_faq_canonicalizer() -> FAQCanonicalizer:
    return FAQCanonicalizer(
        client=get_llm_client(),
        get_cluster_examples_async=_fetch_cluster_context_for_canon,
    )


# ---------------------------------------------------------------------------
# Memory-graph builder — needs neighbor fetch + global list.
# ---------------------------------------------------------------------------
async def _get_cluster_neighbors_async(
    cluster_id: UUID, top_k: int
) -> list[NeighborHit]:
    def _work() -> list[NeighborHit]:
        with sync_session() as session:
            neighbors = glue.get_cluster_neighbors(session, cluster_id, top_k)
            # Build source summary
            src_cluster = next(
                (c for c in glue.list_clusters(session, only_stable=False) if str(c["id"]) == str(cluster_id)),
                None,
            )
            if src_cluster is None:
                return []
            src_examples = [
                r["normalized_text"] or r["raw_text"] or ""
                for r in glue.get_cluster_examples(session, cluster_id, limit=4)
            ]
            src = ClusterSummary(
                cluster_id=UUID(str(src_cluster["id"])),
                canonical=src_cluster.get("canonical_question") or "",
                dominant_intent=_first_intent(src_cluster.get("dominant_intents")),
                dominant_language=_lang(src_cluster.get("dominant_language")),
                examples=src_examples,
            )
            hits: list[NeighborHit] = []
            for n in neighbors:
                ex = [
                    r["normalized_text"] or r["raw_text"] or ""
                    for r in glue.get_cluster_examples(session, n["id"], limit=4)
                ]
                neighbor = ClusterSummary(
                    cluster_id=UUID(str(n["id"])),
                    canonical=n.get("canonical_question") or "",
                    dominant_intent=Intent.OTHER,
                    dominant_language=Language.OTHER,
                    examples=ex,
                )
                cosine_sim = float(1.0 - float(n.get("distance", 1.0)))
                hits.append(
                    NeighborHit(cosine_sim=cosine_sim, source=src, neighbor=neighbor)
                )
            return hits

    return await asyncio.to_thread(_work)


async def _list_clusters_async() -> list[UUID]:
    def _work() -> list[UUID]:
        with sync_session() as session:
            rows = glue.list_clusters(session, only_stable=True)
            return [UUID(str(r["id"])) for r in rows]

    return await asyncio.to_thread(_work)


def make_memory_graph_builder() -> MemoryGraphBuilder:
    return MemoryGraphBuilder(
        client=get_llm_client(),
        get_cluster_neighbors_async=_get_cluster_neighbors_async,
        list_clusters_async=_list_clusters_async,
    )


# ---------------------------------------------------------------------------
# Cluster engine — wires every callable.
# ---------------------------------------------------------------------------
async def _fetch_active_clusters_async() -> list[tuple[UUID, list[float], int]]:
    """Active clusters as ``(cluster_id, centroid, member_count)`` tuples.

    This is the ``FetchActiveClusters`` contract that BOTH consumers index
    positionally — ``IncrementalAssigner.assign`` (``c[0]/c[1]/c[2]``) and
    ``ClusterEngine.rebatch_all`` (``c[0]/c[1]``). Returning richer
    ``ClusterRecord`` objects here broke that contract: once any cluster
    existed, the cluster stage raised ``'ClusterRecord' object is not
    subscriptable`` and the per-cluster canonicalize + memory-edge fan-out
    never ran (0 FAQs, 0 memory edges)."""
    def _work() -> list[tuple[UUID, list[float], int]]:
        with sync_session() as session:
            rows = glue.fetch_active_clusters(session)
            return [
                (
                    UUID(str(r["id"])),
                    _vec_to_list(r.get("centroid")),
                    int(r.get("frequency") or 0),
                )
                for r in rows
            ]

    return await asyncio.to_thread(_work)


async def _persist_assignments_async(
    cluster_updates: list, members: list, new_clusters: list = ()
) -> None:
    """Persist the result of an incremental clustering pass."""
    def _work() -> None:
        with sync_session() as session:
            payload = [
                {
                    "cluster_id": str(m.cluster_id),
                    "question_id": str(m.question_id),
                    "similarity": float(m.similarity),
                }
                for m in members
            ]
            glue.persist_assignments(session, payload)

    await asyncio.to_thread(_work)


async def _fetch_all_embeddings_async():
    def _work():
        from sqlalchemy import text as _sql

        with sync_session() as session:
            rows = (
                session.execute(
                    _sql("SELECT question_id, vector FROM embeddings")
                )
                .mappings()
                .all()
            )
            qids = [UUID(str(r["question_id"])) for r in rows]
            vecs = np.asarray(
                [list(r["vector"]) for r in rows], dtype=np.float32
            ) if rows else np.zeros((0, 1024), dtype=np.float32)
            return qids, vecs

    return await asyncio.to_thread(_work)


async def _persist_batch_async(created, merged, dissolved) -> dict[str, int]:
    """Persist a batch reconciliation result. Placeholder — wires to a SQL
    upsert flow that the DB layer can iterate on."""

    def _work() -> dict[str, int]:
        # Defensive: count only. A full implementation would INSERT new clusters,
        # UPDATE merged centroids, and flip dissolved clusters' is_stable=FALSE.
        with sync_session() as session:
            from sqlalchemy import text as _sql

            for cid, centroid, qids in created or []:
                session.execute(
                    _sql(
                        """
                        INSERT INTO semantic_clusters
                          (id, centroid, dominant_language, dominant_intents,
                           frequency, last_updated, is_stable)
                        VALUES
                          (:id, :centroid, 'other', ARRAY[]::text[], :n,
                           NOW(), TRUE)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {"id": str(cid), "centroid": list(centroid), "n": len(qids)},
                )
                glue.persist_assignments(
                    session,
                    [
                        {"cluster_id": str(cid), "question_id": str(q), "similarity": 1.0}
                        for q in qids
                    ],
                )
            for cid, centroid, qids in merged or []:
                session.execute(
                    _sql(
                        """
                        UPDATE semantic_clusters
                        SET centroid = :centroid, last_updated = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": str(cid), "centroid": list(centroid)},
                )
                glue.persist_assignments(
                    session,
                    [
                        {"cluster_id": str(cid), "question_id": str(q), "similarity": 1.0}
                        for q in qids
                    ],
                )
            for cid in dissolved or []:
                session.execute(
                    _sql(
                        "UPDATE semantic_clusters SET is_stable = FALSE,"
                        " last_updated = NOW() WHERE id = :id"
                    ),
                    {"id": str(cid)},
                )
        return {
            "created": len(created or []),
            "updated": len(merged or []),
            "dissolved": len(dissolved or []),
        }

    return await asyncio.to_thread(_work)


async def _fetch_cluster_examples_for_detail_async(
    cluster_id: UUID,
) -> tuple[ClusterRecord, list[ExtractedQuestion], CanonicalFAQ | None]:
    """Return ``(cluster_record, examples, canonical_faq)`` for a cluster.

    Matches the ``FetchClusterExamples`` contract that ``ClusterEngine.cluster_detail``
    unpacks. (Previously returned only the examples list, which unpacked into
    "too many values to unpack (expected 3)".)
    """
    from sqlalchemy import text as _sql

    def _work() -> tuple[ClusterRecord, list[ExtractedQuestion], CanonicalFAQ | None]:
        with sync_session() as session:
            crow = (
                session.execute(
                    _sql(
                        "SELECT id, label, canonical_question, centroid, "
                        "dominant_language, dominant_intents, frequency, "
                        "last_updated, is_stable FROM semantic_clusters "
                        "WHERE id = :id"
                    ),
                    {"id": str(cluster_id)},
                )
                .mappings()
                .first()
            )
            if crow is None:
                raise LookupError(f"cluster {cluster_id} not found")
            intents = [
                Intent(i)
                if isinstance(i, str) and i in Intent._value2member_map_
                else Intent.OTHER
                for i in (crow.get("dominant_intents") or [])
            ]
            record = ClusterRecord(
                id=UUID(str(crow["id"])),
                label=crow.get("label"),
                canonical_question=crow.get("canonical_question"),
                centroid=_vec_to_list(crow.get("centroid")),
                dominant_language=_lang(crow.get("dominant_language")),
                dominant_intents=intents,
                frequency=int(crow.get("frequency") or 0),
                last_updated=crow["last_updated"],
                representative_question_ids=[],
                is_stable=bool(crow.get("is_stable", True)),
            )

            rows = glue.get_cluster_examples(session, cluster_id, limit=20)
            examples: list[ExtractedQuestion] = []
            for r in rows:
                try:
                    examples.append(
                        ExtractedQuestion(
                            id=UUID(str(r["id"])),
                            call_id=UUID(str(r["call_id"])),
                            utterance_id=(
                                UUID(str(r["utterance_id"]))
                                if r.get("utterance_id")
                                else None
                            ),
                            raw_text=r["raw_text"],
                            normalized_text=r["normalized_text"],
                            english_gloss=r.get("english_gloss"),
                            question_type=r.get("question_type") or "question",
                            intent=r.get("intent") or "other",
                            secondary_intents=r.get("secondary_intents") or [],
                            language=_lang(r.get("language")),
                            confidence=float(r.get("confidence") or 0.0),
                            extracted_at=r.get("extracted_at"),
                        )
                    )
                except Exception:  # pragma: no cover — best-effort
                    continue

            frow = (
                session.execute(
                    _sql(
                        "SELECT id, cluster_id, canonical_question, "
                        "canonical_question_en, suggested_answer, language, "
                        "confidence, version, created_at, updated_at "
                        "FROM canonical_faqs WHERE cluster_id = :id "
                        "ORDER BY version DESC LIMIT 1"
                    ),
                    {"id": str(cluster_id)},
                )
                .mappings()
                .first()
            )
            faq = (
                CanonicalFAQ(
                    id=UUID(str(frow["id"])),
                    cluster_id=UUID(str(frow["cluster_id"])),
                    canonical_question=frow["canonical_question"],
                    canonical_question_en=frow.get("canonical_question_en"),
                    suggested_answer=frow.get("suggested_answer"),
                    language=_lang(frow.get("language")),
                    confidence=float(frow.get("confidence") or 0.0),
                    version=int(frow.get("version") or 1),
                    created_at=frow["created_at"],
                    updated_at=frow["updated_at"],
                )
                if frow is not None
                else None
            )
            return record, examples, faq

    return await asyncio.to_thread(_work)


async def _fetch_cluster_growth_async(window_days: int) -> list[dict[str, Any]]:
    def _work() -> list[dict[str, Any]]:
        with sync_session() as session:
            return glue.fetch_cluster_growth(session, window_days)

    return await asyncio.to_thread(_work)


def make_cluster_engine() -> ClusterEngine:
    inc = IncrementalAssigner(
        fetch_active_clusters=_fetch_active_clusters_async,
        persist_assignments=_persist_assignments_async,
    )
    return ClusterEngine(
        incremental=inc,
        fetch_all_embeddings=_fetch_all_embeddings_async,
        persist_batch=_persist_batch_async,
        fetch_cluster_examples=_fetch_cluster_examples_for_detail_async,
        fetch_cluster_growth=_fetch_cluster_growth_async,
        fetch_active_clusters=_fetch_active_clusters_async,
    )


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------
def _first_intent(raw) -> Intent:
    if not raw:
        return Intent.OTHER
    for r in raw:
        if isinstance(r, str) and r in Intent._value2member_map_:
            return Intent(r)
    return Intent.OTHER


def _vec_to_list(value) -> list[float]:
    """Coerce a pgvector value (ndarray | list | None) to a plain float list.

    With the pgvector codec registered, ``vector`` columns load as numpy
    arrays, so the old ``list(value or [])`` idiom raised "truth value of an
    array is ambiguous". This is None-safe and array-safe.
    """
    if value is None:
        return []
    return [float(x) for x in value]


def _lang(raw) -> Language:
    if not raw:
        return Language.OTHER
    if isinstance(raw, Language):
        return raw
    if isinstance(raw, str) and raw in Language._value2member_map_:
        return Language(raw)
    return Language.OTHER


__all__ = [
    "get_llm_client",
    "get_embedding_service",
    "make_llm_extractor",
    "make_faq_canonicalizer",
    "make_memory_graph_builder",
    "make_cluster_engine",
]
