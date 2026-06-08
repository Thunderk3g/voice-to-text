"""Pure metric functions for clustering granularity diagnostics.

No I/O, no DB — every function takes plain Python data so it is trivially
unit-testable. Used by ``app/scripts/phase_a_diagnostics.py``.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Sequence

import numpy as np


def normalized_entropy(labels: Sequence[str]) -> float:
    """Shannon entropy of the label distribution, normalized to [0, 1].

    0.0 = all one label (pure); 1.0 = uniform across the distinct labels seen.
    Empty or single-label input returns 0.0.
    """
    n = len(labels)
    if n == 0:
        return 0.0
    counts = Counter(labels)
    if len(counts) <= 1:
        return 0.0
    h = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return h / math.log2(len(counts))


def intent_purity(labels: Sequence[str]) -> float:
    """Fraction of items belonging to the single most common label.

    1.0 = pure; lower = more mixed. Empty input returns 1.0 (vacuously pure).
    """
    n = len(labels)
    if n == 0:
        return 1.0
    counts = Counter(labels)
    return max(counts.values()) / n


def size_stats(sizes: Sequence[int]) -> dict[str, float]:
    """Summary statistics over a list of cluster sizes.

    ``p90`` uses the ceiling-based nearest-rank method (the 90th-percentile
    value is an actual element of the input, not interpolated). ``mean`` is
    rounded to 2 decimal places for display; all other values are exact.
    Empty input returns count/min/max/p90 = 0 and mean/median = 0.0.
    """
    if not sizes:
        return {"count": 0, "min": 0, "max": 0, "mean": 0.0, "median": 0.0, "p90": 0}
    ordered = sorted(sizes)
    p90_idx = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 2),
        "median": float(median(ordered)),
        "p90": ordered[p90_idx],
    }


def mean_cosine_distance_to_centroid(
    vectors: Sequence[Sequence[float]],
    centroid: Sequence[float],
) -> float:
    """Mean cosine distance (1 - cosine similarity) of members to the centroid.

    Higher = members are more spread out = the cluster is internally diverse.
    Empty ``vectors`` returns 0.0. A zero-norm member vector is treated as
    maximally divergent (distance 1.0 from any centroid).

    Inputs are assumed finite: the caller pulls vectors from pgvector, which
    stores finite floats. NaN/inf inputs are not guarded and will propagate.
    """
    if len(vectors) == 0:
        return 0.0
    mat = np.asarray(vectors, dtype=np.float64)
    c = np.asarray(centroid, dtype=np.float64)
    c_norm = np.linalg.norm(c)
    if c_norm == 0:
        return 0.0
    row_norms = np.linalg.norm(mat, axis=1)
    row_norms[row_norms == 0] = 1.0
    cos_sim = (mat @ c) / (row_norms * c_norm)
    cos_sim = np.clip(cos_sim, -1.0, 1.0)
    return float(np.mean(1.0 - cos_sim))


__all__ = [
    "normalized_entropy",
    "intent_purity",
    "size_stats",
    "mean_cosine_distance_to_centroid",
]
