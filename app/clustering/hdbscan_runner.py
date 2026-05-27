"""
Pure HDBSCAN runner.

We rely on the fact that for *L2-normalized* vectors:
    cosine_distance = 0.5 * euclidean_distance^2
so HDBSCAN with `metric='euclidean'` over normalized vectors recovers the
desired cosine-based clustering and keeps `prediction_data=True` available.
"""

from __future__ import annotations

import numpy as np

from app.utils.vector import l2_normalize


def run_hdbscan(
    vectors: np.ndarray,
    min_cluster_size: int,
    min_samples: int,
) -> np.ndarray:
    """Cluster `vectors` and return a label array of shape (N,).

    Labels: integer cluster ids; -1 means noise.
    Vectors are L2-normalized before clustering so euclidean ≅ cosine.
    """
    import hdbscan  # local import; heavy native dep

    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] == 0:
        return np.array([], dtype=np.int64)

    arr = l2_normalize(arr, axis=1)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=max(2, int(min_cluster_size)),
        min_samples=max(1, int(min_samples)),
        metric="euclidean",
        prediction_data=True,
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(arr)
    return np.asarray(labels, dtype=np.int64)


__all__ = ["run_hdbscan"]
