import math

import pytest

from app.diagnostics.cluster_metrics import (
    intent_purity,
    mean_cosine_distance_to_centroid,
    normalized_entropy,
    size_stats,
)


def test_normalized_entropy_pure_cluster_is_zero():
    assert normalized_entropy(["a", "a", "a"]) == 0.0


def test_normalized_entropy_empty_is_zero():
    assert normalized_entropy([]) == 0.0


def test_normalized_entropy_uniform_two_labels_is_one():
    assert normalized_entropy(["a", "b"]) == pytest.approx(1.0)


def test_normalized_entropy_uniform_four_labels_is_one():
    assert normalized_entropy(["a", "b", "c", "d"]) == pytest.approx(1.0)


def test_normalized_entropy_skewed_is_between_zero_and_one():
    h = normalized_entropy(["a", "a", "a", "b"])
    assert 0.0 < h < 1.0


def test_intent_purity_pure_is_one():
    assert intent_purity(["x", "x", "x"]) == 1.0


def test_intent_purity_half_is_half():
    assert intent_purity(["x", "x", "y", "y"]) == pytest.approx(0.5)


def test_intent_purity_empty_is_one():
    assert intent_purity([]) == 1.0


def test_size_stats_basic():
    stats = size_stats([1, 2, 3, 4, 100])
    assert stats["count"] == 5
    assert stats["max"] == 100
    assert stats["min"] == 1
    assert stats["median"] == 3


def test_size_stats_empty():
    stats = size_stats([])
    assert stats["count"] == 0
    assert stats["max"] == 0


def test_mean_cosine_distance_identical_vectors_is_zero():
    vecs = [[1.0, 0.0], [1.0, 0.0]]
    centroid = [1.0, 0.0]
    assert mean_cosine_distance_to_centroid(vecs, centroid) == pytest.approx(0.0, abs=1e-6)


def test_mean_cosine_distance_orthogonal_is_one():
    vecs = [[0.0, 1.0]]
    centroid = [1.0, 0.0]
    assert mean_cosine_distance_to_centroid(vecs, centroid) == pytest.approx(1.0, abs=1e-6)


def test_mean_cosine_distance_empty_is_zero():
    assert mean_cosine_distance_to_centroid([], [1.0, 0.0]) == 0.0
