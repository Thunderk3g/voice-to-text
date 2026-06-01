"""
Cluster-quality metrics.

Computes silhouette score across a labeled embedding set. Returns a single
mean score plus a count of labeled samples so callers can decide whether to
trust the number (silhouette requires >=2 clusters and >=1 sample per).

We use scikit-learn's silhouette_score with cosine metric to stay consistent
with how pgvector ranks neighbors.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

import structlog

logger = structlog.get_logger(__name__)


def cluster_silhouette(
    vectors: Sequence[Sequence[float]],
    labels: Sequence[int | str],
    *,
    max_samples: int = 5000,
) -> tuple[float, int]:
    """Return (mean_silhouette, sample_count).

    If the input is too small or degenerate (single label, all same point),
    returns (0.0, 0). Caps to ``max_samples`` rows because silhouette is
    O(N^2) and we'd rather have a good estimate than a long wait.
    """
    if not vectors or not labels:
        return 0.0, 0

    arr = np.asarray(vectors, dtype=np.float32)
    lbl = np.asarray(list(labels))
    if arr.ndim != 2 or arr.shape[0] != lbl.shape[0]:
        return 0.0, 0

    # Need at least two distinct labels.
    unique_labels = set(lbl.tolist())
    if len(unique_labels) < 2:
        return 0.0, int(arr.shape[0])

    if arr.shape[0] > max_samples:
        rng = np.random.default_rng(seed=42)
        idx = rng.choice(arr.shape[0], size=max_samples, replace=False)
        arr = arr[idx]
        lbl = lbl[idx]

    try:
        from sklearn.metrics import silhouette_score

        score = float(
            silhouette_score(arr, lbl, metric="cosine", sample_size=None)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("silhouette_failed", error=str(exc))
        return 0.0, int(arr.shape[0])

    return score, int(arr.shape[0])


__all__ = ["cluster_silhouette"]
