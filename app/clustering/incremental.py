"""
Incremental cluster assignment.

For each new embedding we compute cosine similarity (vectorized) to every
active cluster centroid. If the best similarity meets the configured
threshold, we assign; otherwise the embedding is deferred for the next
periodic batch HDBSCAN run.

All DB access is performed through injected callables — this module does
not import SQLAlchemy or write SQL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import clusters_assigned
from app.models.schemas import ClusterMember, EmbeddingRecord
from app.utils.vector import l2_normalize

from app.clustering.centroids import update_centroid

logger = get_logger(__name__)


# Injected callable signatures ------------------------------------------------
#   fetch_active_clusters() -> list[tuple[UUID, list[float], int]]
#       returns (cluster_id, centroid_vector, current_member_count)
#   persist_assignments(members, centroid_updates) -> None
#       members: list[ClusterMember]
#       centroid_updates: list[tuple[UUID, list[float], int]]
FetchActiveClusters = Callable[[], Awaitable[list[tuple[UUID, list[float], int]]]]
PersistAssignments = Callable[
    [list[ClusterMember], list[tuple[UUID, list[float], int]]],
    Awaitable[None],
]


class IncrementalAssigner:
    """Assigns new embeddings to existing clusters by centroid cosine."""

    def __init__(
        self,
        fetch_active_clusters: FetchActiveClusters,
        persist_assignments: PersistAssignments,
        *,
        threshold: float | None = None,
    ) -> None:
        self._fetch = fetch_active_clusters
        self._persist = persist_assignments
        settings = get_settings()
        self._threshold = (
            float(threshold)
            if threshold is not None
            else float(settings.cluster_incremental_threshold)
        )

    @property
    def threshold(self) -> float:
        return self._threshold

    async def assign(
        self,
        embeddings: list[EmbeddingRecord],
    ) -> list[ClusterMember]:
        """Assign new embeddings to nearest existing cluster (if above threshold).

        Returns a list of ClusterMember for those that were assigned. Embeddings
        whose best match is below threshold are *deferred* (not returned) for the
        next periodic batch run.

        Side effects:
            - Calls `persist_assignments(members, centroid_updates)` if any
              assignments occurred.
            - Bumps `clusters_assigned.labels(mode='incremental')`.
        """
        if not embeddings:
            return []

        clusters = await self._fetch()
        if not clusters:
            # Cold start — nothing to assign against; defer all.
            return []

        # Stack centroids and incoming vectors into matrices for vectorized cos.
        centroid_ids = [c[0] for c in clusters]
        centroid_mat = np.asarray(
            [c[1] for c in clusters], dtype=np.float32
        )  # (K, D)
        centroid_counts = np.asarray(
            [c[2] for c in clusters], dtype=np.int64
        )

        emb_mat = np.asarray(
            [e.vector for e in embeddings], dtype=np.float32
        )  # (N, D)

        if centroid_mat.size == 0 or emb_mat.size == 0:
            return []

        # Defensive normalization (centroids stored normalized; embeddings too).
        centroid_n = l2_normalize(centroid_mat, axis=1)
        emb_n = l2_normalize(emb_mat, axis=1)

        sim = emb_n @ centroid_n.T  # (N, K)
        best_idx = sim.argmax(axis=1)  # (N,)
        best_sim = sim[np.arange(sim.shape[0]), best_idx]  # (N,)

        accept_mask = best_sim >= self._threshold
        accept_indices = np.where(accept_mask)[0]

        if accept_indices.size == 0:
            return []

        now = datetime.now(timezone.utc)
        members: list[ClusterMember] = []
        # Group accepted embeddings by target cluster to update centroids in bulk.
        grouped_vecs: dict[int, list[np.ndarray]] = {}
        for i in accept_indices.tolist():
            k = int(best_idx[i])
            cluster_id = centroid_ids[k]
            members.append(
                ClusterMember(
                    cluster_id=cluster_id,
                    question_id=embeddings[i].question_id,
                    similarity=float(best_sim[i]),
                    assigned_at=now,
                )
            )
            grouped_vecs.setdefault(k, []).append(emb_n[i])

        centroid_updates: list[tuple[UUID, list[float], int]] = []
        for k, vecs in grouped_vecs.items():
            new_arr = np.vstack(vecs)
            new_centroid = update_centroid(
                centroid_n[k], int(centroid_counts[k]), new_arr
            )
            new_count = int(centroid_counts[k]) + new_arr.shape[0]
            centroid_updates.append(
                (centroid_ids[k], new_centroid.tolist(), new_count)
            )

        await self._persist(members, centroid_updates)

        clusters_assigned.labels(mode="incremental").inc(len(members))
        logger.info(
            "incremental_assign_done",
            assigned=len(members),
            deferred=int((~accept_mask).sum()),
            threshold=self._threshold,
        )

        return members


__all__ = ["IncrementalAssigner", "FetchActiveClusters", "PersistAssignments"]
