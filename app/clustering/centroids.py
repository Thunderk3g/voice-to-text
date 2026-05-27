"""
Centroid math for the cluster engine.

Centroids are stored *L2-normalized* (unit sphere). Updates use a true
running mean over the full member set, then re-normalize so the centroid
stays on the unit sphere — this keeps cosine semantics consistent with the
embedding store.
"""

from __future__ import annotations

from typing import Sequence
from uuid import UUID

import numpy as np

from app.utils.vector import l2_normalize


def compute_centroid(vectors: np.ndarray) -> np.ndarray:
    """Mean-of-vectors, L2-normalized. Empty input → zero vector."""
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return np.zeros((0,), dtype=np.float32)
    mean = arr.mean(axis=0)
    return l2_normalize(mean.reshape(1, -1), axis=1)[0]


def update_centroid(
    old_centroid: np.ndarray,
    old_n: int,
    new_vectors: np.ndarray,
) -> np.ndarray:
    """Running mean update: combine old centroid * old_n with new vectors.

    Returns the L2-normalized updated centroid. Numerically this is the
    same as computing the mean over all members so long as the stored
    centroid represents `old_n` member vectors.

    Note: because we re-normalize, we use the un-normalized running mean
    as an internal step; that is why `old_centroid` should be the *mean*
    (or the normalized mean — both work because we re-normalize) of the
    `old_n` member vectors.
    """
    new = np.asarray(new_vectors, dtype=np.float32)
    if new.ndim == 1:
        new = new.reshape(1, -1)

    n_new = new.shape[0] if new.size else 0
    total = max(0, int(old_n)) + n_new

    if total == 0:
        return np.asarray(old_centroid, dtype=np.float32)

    old = np.asarray(old_centroid, dtype=np.float32).reshape(-1)
    if old.size == 0 and n_new > 0:
        combined = new.sum(axis=0) / n_new
    elif n_new == 0:
        combined = old
    else:
        # Weighted mean. `old` represents old_n members.
        combined = (old * float(old_n) + new.sum(axis=0)) / float(total)

    return l2_normalize(combined.reshape(1, -1), axis=1)[0]


def pick_representatives(
    vectors: np.ndarray,
    ids: Sequence[UUID],
    centroid: np.ndarray,
    k: int = 6,
) -> list[UUID]:
    """Return up to `k` ids whose vectors are closest (cosine) to centroid.

    Vectors are assumed L2-normalized but we normalize defensively.
    """
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0 or len(ids) == 0:
        return []

    arr_n = l2_normalize(arr, axis=1)
    c = np.asarray(centroid, dtype=np.float32).reshape(-1)
    if c.size == 0:
        return list(ids[:k])
    c_n = l2_normalize(c.reshape(1, -1), axis=1)[0]

    sims = arr_n @ c_n  # (N,)
    # argsort descending; vectorized — no Python loop over embeddings.
    order = np.argsort(-sims)[: max(0, int(k))]
    ids_arr = np.asarray(ids, dtype=object)
    return [ids_arr[i] for i in order.tolist()]


__all__ = ["compute_centroid", "update_centroid", "pick_representatives"]
