"""
Unit tests for the pure clustering primitives:
    - HDBSCAN runner on 3 Gaussian blobs
    - centroid running-mean convergence
    - representative selection by cosine
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from app.clustering.centroids import (
    compute_centroid,
    pick_representatives,
    update_centroid,
)
from app.utils.vector import l2_normalize


# Skip the whole module if hdbscan isn't available in the runtime.
hdbscan = pytest.importorskip("hdbscan")

from app.clustering.hdbscan_runner import run_hdbscan  # noqa: E402


def _three_gaussians(seed: int = 7, n: int = 60, d: int = 32) -> np.ndarray:
    """Synthetic 3-cluster dataset on the unit sphere."""
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(3, d)).astype(np.float32)
    centers = l2_normalize(centers, axis=1)

    pts = []
    for c in centers:
        noise = rng.normal(scale=0.05, size=(n, d)).astype(np.float32)
        pts.append(c + noise)
    arr = np.vstack(pts)
    return l2_normalize(arr, axis=1).astype(np.float32)


def test_hdbscan_finds_three_clusters():
    X = _three_gaussians()
    labels = run_hdbscan(X, min_cluster_size=8, min_samples=4)

    distinct = sorted({int(l) for l in labels.tolist() if int(l) != -1})
    assert len(distinct) == 3, f"expected 3 clusters, got {distinct}"

    # At most a small fraction of points should be flagged as noise.
    noise_frac = float((labels == -1).mean())
    assert noise_frac <= 0.20


def test_compute_and_update_centroid_converge():
    rng = np.random.default_rng(13)
    d = 16
    true_center = l2_normalize(rng.normal(size=(d,)).astype(np.float32))

    vecs = l2_normalize(
        true_center + rng.normal(scale=0.05, size=(50, d)).astype(np.float32),
        axis=1,
    )

    # Batch centroid:
    c_batch = compute_centroid(vecs)
    assert c_batch.shape == (d,)
    assert pytest.approx(1.0, abs=1e-5) == float(np.linalg.norm(c_batch))
    assert float(np.dot(c_batch, true_center)) > 0.99

    # Streaming update — split into chunks and verify convergence to batch.
    c_running = compute_centroid(vecs[:5])
    n_running = 5
    for start in range(5, 50, 5):
        chunk = vecs[start : start + 5]
        c_running = update_centroid(c_running, n_running, chunk)
        n_running += chunk.shape[0]

    # Running mean (with re-normalize each step) should agree with batch to
    # high precision because every member has unit norm of similar direction.
    assert float(np.dot(c_running, c_batch)) > 0.999


def test_update_centroid_handles_empty_old():
    new = np.random.default_rng(0).normal(size=(4, 8)).astype(np.float32)
    c = update_centroid(np.zeros((0,), dtype=np.float32), 0, new)
    assert c.shape == (8,)
    assert pytest.approx(1.0, abs=1e-5) == float(np.linalg.norm(c))


def test_pick_representatives_returns_closest():
    rng = np.random.default_rng(42)
    d = 8
    centroid = l2_normalize(rng.normal(size=(d,)).astype(np.float32))

    # 10 vectors, first three are near the centroid, rest are random.
    near = l2_normalize(
        centroid + rng.normal(scale=0.02, size=(3, d)).astype(np.float32),
        axis=1,
    )
    far = l2_normalize(rng.normal(size=(7, d)).astype(np.float32), axis=1)
    vecs = np.vstack([near, far])
    ids = [uuid4() for _ in range(10)]

    reps = pick_representatives(vecs, ids, centroid, k=3)
    assert set(reps) == set(ids[:3])
