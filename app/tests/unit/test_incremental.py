"""
Unit tests for IncrementalAssigner threshold logic with stub callables.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import numpy as np
import pytest

from app.clustering.incremental import IncrementalAssigner
from app.models.schemas import ClusterMember, EmbeddingRecord
from app.utils.vector import l2_normalize


def _make_emb(vec: np.ndarray) -> EmbeddingRecord:
    v = l2_normalize(vec.reshape(1, -1), axis=1)[0]
    return EmbeddingRecord(
        question_id=uuid4(),
        model="test-e5",
        dim=int(v.shape[0]),
        vector=v.tolist(),
    )


class _Stub:
    def __init__(self, clusters):
        self._clusters = clusters
        self.persisted_members: list[ClusterMember] = []
        self.persisted_updates: list[tuple] = []

    async def fetch(self):
        return list(self._clusters)

    async def persist(self, members, updates):
        self.persisted_members.extend(members)
        self.persisted_updates.extend(updates)


@pytest.mark.asyncio
async def test_assigns_when_similarity_above_threshold():
    d = 8
    rng = np.random.default_rng(1)
    centroid = l2_normalize(rng.normal(size=(d,)).astype(np.float32))
    cluster_id = uuid4()

    stub = _Stub([(cluster_id, centroid.tolist(), 5)])
    assigner = IncrementalAssigner(stub.fetch, stub.persist, threshold=0.78)

    # Very close to centroid → similarity ~ 1.0
    emb = _make_emb(centroid + 0.01 * rng.normal(size=(d,)).astype(np.float32))
    members = await assigner.assign([emb])

    assert len(members) == 1
    assert members[0].cluster_id == cluster_id
    assert members[0].similarity >= 0.78
    assert stub.persisted_members == members
    assert len(stub.persisted_updates) == 1
    # centroid update tuple shape
    cid, new_centroid, new_count = stub.persisted_updates[0]
    assert cid == cluster_id
    assert len(new_centroid) == d
    assert new_count == 6


@pytest.mark.asyncio
async def test_defers_when_below_threshold():
    d = 8
    rng = np.random.default_rng(2)
    centroid = l2_normalize(rng.normal(size=(d,)).astype(np.float32))

    stub = _Stub([(uuid4(), centroid.tolist(), 10)])
    assigner = IncrementalAssigner(stub.fetch, stub.persist, threshold=0.95)

    # Orthogonal vector → similarity ~ 0
    orth = rng.normal(size=(d,)).astype(np.float32)
    orth = orth - np.dot(orth, centroid) * centroid
    emb = _make_emb(orth)

    members = await assigner.assign([emb])
    assert members == []
    assert stub.persisted_members == []
    assert stub.persisted_updates == []


@pytest.mark.asyncio
async def test_cold_start_returns_empty():
    stub = _Stub([])  # no active clusters at all
    assigner = IncrementalAssigner(stub.fetch, stub.persist, threshold=0.78)

    emb = _make_emb(np.ones(8, dtype=np.float32))
    members = await assigner.assign([emb])
    assert members == []


@pytest.mark.asyncio
async def test_picks_best_of_multiple_clusters():
    d = 8
    rng = np.random.default_rng(3)
    c1 = l2_normalize(rng.normal(size=(d,)).astype(np.float32))
    c2 = l2_normalize(rng.normal(size=(d,)).astype(np.float32))

    id1, id2 = uuid4(), uuid4()
    stub = _Stub([(id1, c1.tolist(), 3), (id2, c2.tolist(), 7)])
    assigner = IncrementalAssigner(stub.fetch, stub.persist, threshold=0.5)

    # Embedding pointed strongly at c2
    emb = _make_emb(c2 + 0.01 * rng.normal(size=(d,)).astype(np.float32))
    members = await assigner.assign([emb])

    assert len(members) == 1
    assert members[0].cluster_id == id2
