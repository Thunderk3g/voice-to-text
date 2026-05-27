"""
ClusterEngine — orchestrates incremental + periodic batch clustering.

Reconciliation strategy (batch ↔ existing):
    1. Run HDBSCAN over *all* current embeddings.
    2. Compute the centroid of each new HDBSCAN cluster (noise label = -1
       skipped).
    3. For every new cluster, find the best existing cluster centroid by
       cosine similarity.
         - sim >= 0.90 → MERGE: new members folded into the existing cluster,
           centroid recomputed from full membership.
         - else        → CREATE: a brand-new semantic_cluster row.
    4. Any existing cluster whose entire membership now lives in a different
       cluster (i.e. zero retained members after batch) is marked DISSOLVED.

This module has no SQL — all DB I/O is via injected async callables.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import clusters_assigned
from app.models.enums import Intent, Language
from app.models.schemas import (
    ClusterDetail,
    ClusterMember,
    ClusterRecord,
    EmbeddingRecord,
    ExtractedQuestion,
)
from app.utils.vector import l2_normalize

from app.clustering.centroids import compute_centroid, pick_representatives
from app.clustering.hdbscan_runner import run_hdbscan
from app.clustering.incremental import IncrementalAssigner

logger = get_logger(__name__)


# Injected callable signatures ------------------------------------------------
# fetch_all_embeddings() -> tuple[list[UUID], np.ndarray]
#     returns (question_ids, vectors[N,D])
FetchAllEmbeddings = Callable[[], Awaitable[tuple[list[UUID], np.ndarray]]]

# persist_batch_assignments(
#     created: list[tuple[UUID, list[float], list[UUID]]],     # (new_cluster_id, centroid, question_ids)
#     merged: list[tuple[UUID, list[float], list[UUID]]],      # (existing_cluster_id, new_centroid, added_question_ids)
#     dissolved: list[UUID],
# ) -> dict[str,int]
PersistBatch = Callable[
    [
        list[tuple[UUID, list[float], list[UUID]]],
        list[tuple[UUID, list[float], list[UUID]]],
        list[UUID],
    ],
    Awaitable[dict[str, int]],
]

# fetch_cluster_examples(cluster_id) -> tuple[ClusterRecord, list[ExtractedQuestion], CanonicalFAQ|None]
FetchClusterExamples = Callable[[UUID], Awaitable[tuple]]

# fetch_cluster_growth(window_days) -> list[dict]
FetchClusterGrowth = Callable[[int], Awaitable[list[dict]]]

# fetch_active_clusters() -> list[tuple[UUID, list[float], int]]   (same as incremental)
FetchActiveClusters = Callable[[], Awaitable[list[tuple[UUID, list[float], int]]]]


# Merge-existing centroid threshold used during batch reconciliation.
_MERGE_THRESHOLD = 0.90


class ClusterEngine:
    """High-level façade combining incremental + batch HDBSCAN."""

    def __init__(
        self,
        incremental: IncrementalAssigner,
        fetch_all_embeddings: FetchAllEmbeddings,
        persist_batch: PersistBatch,
        fetch_cluster_examples: FetchClusterExamples,
        fetch_cluster_growth: FetchClusterGrowth,
        fetch_active_clusters: FetchActiveClusters,
    ) -> None:
        self._inc = incremental
        self._fetch_all = fetch_all_embeddings
        self._persist_batch = persist_batch
        self._fetch_examples = fetch_cluster_examples
        self._fetch_growth = fetch_cluster_growth
        self._fetch_active = fetch_active_clusters
        self._settings = get_settings()

    # ------------------------------------------------------------------
    async def assign_incremental(
        self, embeddings: list[EmbeddingRecord]
    ) -> list[ClusterMember]:
        """Delegate to the IncrementalAssigner."""
        return await self._inc.assign(embeddings)

    # ------------------------------------------------------------------
    async def rebatch_all(self) -> dict[str, int]:
        """Run HDBSCAN on the full corpus and reconcile with existing clusters.

        Returns {"created": int, "updated": int, "dissolved": int}.
        """
        from uuid import uuid4

        qids, vectors = await self._fetch_all()
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] == 0:
            return {"created": 0, "updated": 0, "dissolved": 0}

        labels = run_hdbscan(
            vectors,
            min_cluster_size=self._settings.hdbscan_min_cluster_size,
            min_samples=self._settings.hdbscan_min_samples,
        )

        existing = await self._fetch_active()
        existing_ids = [c[0] for c in existing]
        existing_centroids = (
            l2_normalize(
                np.asarray([c[1] for c in existing], dtype=np.float32), axis=1
            )
            if existing
            else np.zeros((0, vectors.shape[1]), dtype=np.float32)
        )

        # Group new HDBSCAN labels (skip -1 noise) vectorized.
        unique_labels = np.unique(labels)
        new_clusters: list[tuple[np.ndarray, np.ndarray]] = []  # (centroid, member_idx)
        for lbl in unique_labels.tolist():
            if lbl == -1:
                continue
            member_idx = np.where(labels == lbl)[0]
            if member_idx.size == 0:
                continue
            member_vecs = vectors[member_idx]
            centroid = compute_centroid(member_vecs)
            new_clusters.append((centroid, member_idx))

        created: list[tuple[UUID, list[float], list[UUID]]] = []
        merged: list[tuple[UUID, list[float], list[UUID]]] = []
        touched_existing: set[UUID] = set()

        if new_clusters and existing_centroids.shape[0] > 0:
            new_centroids = np.vstack([c for c, _ in new_clusters])
            sim = new_centroids @ existing_centroids.T  # (M_new, K_old)
            best_existing = sim.argmax(axis=1)
            best_sim = sim[np.arange(sim.shape[0]), best_existing]
        else:
            best_existing = np.zeros(len(new_clusters), dtype=np.int64)
            best_sim = np.zeros(len(new_clusters), dtype=np.float32)

        for j, (centroid, member_idx) in enumerate(new_clusters):
            member_qids = [qids[int(i)] for i in member_idx.tolist()]
            if existing_centroids.shape[0] > 0 and best_sim[j] >= _MERGE_THRESHOLD:
                target_id = existing_ids[int(best_existing[j])]
                merged.append((target_id, centroid.tolist(), member_qids))
                touched_existing.add(target_id)
            else:
                created.append((uuid4(), centroid.tolist(), member_qids))

        # Dissolved: existing clusters not touched AND not stable enough.
        # We treat "not touched" + "didn't survive HDBSCAN" as dissolved.
        # Callers (DB layer) may further check is_stable before marking inactive.
        dissolved = [cid for cid in existing_ids if cid not in touched_existing]

        result = await self._persist_batch(created, merged, dissolved)

        clusters_assigned.labels(mode="batch").inc(
            sum(len(m[2]) for m in created) + sum(len(m[2]) for m in merged)
        )
        logger.info(
            "rebatch_done",
            created=len(created),
            updated=len(merged),
            dissolved=len(dissolved),
            noise=int((labels == -1).sum()),
        )
        # Defensive defaults if persist returned partials.
        out = {
            "created": int(result.get("created", len(created))),
            "updated": int(result.get("updated", len(merged))),
            "dissolved": int(result.get("dissolved", len(dissolved))),
        }
        return out

    # ------------------------------------------------------------------
    async def cluster_detail(self, cluster_id: UUID) -> ClusterDetail:
        """Build a ClusterDetail aggregate. DB I/O is delegated."""
        record, examples, faq = await self._fetch_examples(cluster_id)
        if not isinstance(record, ClusterRecord):  # defensive
            raise TypeError("fetch_cluster_examples must return (ClusterRecord, ...)")

        examples_list: list[ExtractedQuestion] = list(examples or [])

        intent_dist: dict[Intent, int] = dict(
            Counter(q.intent for q in examples_list if q.intent is not None)
        )
        lang_dist: dict[Language, int] = dict(
            Counter(q.language for q in examples_list if q.language is not None)
        )

        return ClusterDetail(
            cluster=record,
            canonical_faq=faq,
            examples=examples_list,
            intent_distribution=intent_dist,
            language_distribution=lang_dist,
        )


__all__ = ["ClusterEngine", "pick_representatives"]
