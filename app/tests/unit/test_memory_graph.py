"""Unit tests for ``app.services.memory_graph.builder.MemoryGraphBuilder``.

We mock the vLLM HTTP layer and inject fake neighbor / list callables to
verify the sim threshold filter, the weight threshold, the per-cluster
cap, and the fan-out for ``rebuild_global``.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.models.enums import EdgeRelation, Intent, Language
from app.services.llm.groq_client import GroqClient
from app.services.memory_graph.builder import (
    ClusterSummary,
    MemoryGraphBuilder,
    NeighborHit,
)


def _openai_chat_payload(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": get_settings().llm_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _summary(cluster_id, canonical: str = "Q") -> ClusterSummary:
    return ClusterSummary(
        cluster_id=cluster_id,
        canonical=canonical,
        dominant_intent=Intent.PREMIUM_PAYMENT,
        dominant_language=Language.ENGLISH,
        examples=["example 1", "example 2"],
    )


@pytest.mark.asyncio
async def test_min_sim_filter_drops_low_neighbors() -> None:
    """Neighbors with cosine < settings.memory_edge_min_sim must be dropped
    *before* the LLM is called."""
    settings = get_settings()
    source_id = uuid4()
    source = _summary(source_id, "source")

    # One neighbor above threshold, two below.
    above = NeighborHit(
        cosine_sim=settings.memory_edge_min_sim + 0.1,
        source=source,
        neighbor=_summary(uuid4(), "above"),
    )
    below_1 = NeighborHit(
        cosine_sim=settings.memory_edge_min_sim - 0.01,
        source=source,
        neighbor=_summary(uuid4(), "below1"),
    )
    below_2 = NeighborHit(
        cosine_sim=0.1,
        source=source,
        neighbor=_summary(uuid4(), "below2"),
    )

    async def get_neighbors(cid, top_k):
        assert cid == source_id
        assert top_k == settings.memory_edge_top_k
        return [above, below_1, below_2]

    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_chat_payload(
                    json.dumps(
                        {
                            "has_relation": True,
                            "relation": "related_to",
                            "weight": 0.8,
                            "reason": "shared topic",
                        }
                    )
                ),
            )
        )
        client = GroqClient()
        builder = MemoryGraphBuilder(client, get_neighbors)
        edges = await builder.build_edges_for(source_id)
        await client.aclose()

    assert len(edges) == 1
    assert edges[0].relation == EdgeRelation.RELATED_TO
    assert edges[0].weight == pytest.approx(0.8)
    assert edges[0].source_cluster_id == source_id
    # Exactly one LLM call — the two below-threshold neighbors must be skipped.
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_weight_threshold_drops_weak_relations() -> None:
    """LLM ``weight < 0.5`` or ``has_relation=False`` should be dropped."""
    settings = get_settings()
    source_id = uuid4()
    source = _summary(source_id, "source")

    n1 = NeighborHit(cosine_sim=0.8, source=source, neighbor=_summary(uuid4(), "n1"))
    n2 = NeighborHit(cosine_sim=0.8, source=source, neighbor=_summary(uuid4(), "n2"))
    n3 = NeighborHit(cosine_sim=0.8, source=source, neighbor=_summary(uuid4(), "n3"))

    async def get_neighbors(cid, top_k):
        return [n1, n2, n3]

    responses = iter(
        [
            # n1: weak weight → drop
            json.dumps(
                {"has_relation": True, "relation": "related_to", "weight": 0.4}
            ),
            # n2: explicit has_relation=False → drop
            json.dumps(
                {"has_relation": False, "relation": "related_to", "weight": 0.9}
            ),
            # n3: accepted
            json.dumps(
                {
                    "has_relation": True,
                    "relation": "leads_to",
                    "weight": 0.75,
                    "reason": "journey",
                }
            ),
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openai_chat_payload(next(responses)))

    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(side_effect=_handler)
        client = GroqClient()
        builder = MemoryGraphBuilder(client, get_neighbors)
        edges = await builder.build_edges_for(source_id)
        await client.aclose()

    assert len(edges) == 1
    assert edges[0].relation == EdgeRelation.LEADS_TO


@pytest.mark.asyncio
async def test_caps_edges_at_max_per_cluster() -> None:
    """We must stop calling the LLM once we've accepted ``max_per_cluster``."""
    settings = get_settings()
    cap = settings.memory_edge_max_per_cluster
    source_id = uuid4()
    source = _summary(source_id, "source")

    neighbors = [
        NeighborHit(
            cosine_sim=0.9,
            source=source,
            neighbor=_summary(uuid4(), f"n{i}"),
        )
        for i in range(cap + 4)
    ]

    async def get_neighbors(cid, top_k):
        return neighbors

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_openai_chat_payload(
                json.dumps(
                    {
                        "has_relation": True,
                        "relation": "co_occurs",
                        "weight": 0.7,
                    }
                )
            ),
        )

    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        route = router.post("/chat/completions").mock(side_effect=_handler)
        client = GroqClient()
        builder = MemoryGraphBuilder(client, get_neighbors)
        edges = await builder.build_edges_for(source_id)
        await client.aclose()

    assert len(edges) == cap
    # We should never call the LLM for the extras beyond the cap.
    assert route.call_count == cap


@pytest.mark.asyncio
async def test_skips_self_loop() -> None:
    settings = get_settings()
    source_id = uuid4()
    source = _summary(source_id, "source")

    self_hit = NeighborHit(cosine_sim=1.0, source=source, neighbor=source)
    other = NeighborHit(
        cosine_sim=0.9, source=source, neighbor=_summary(uuid4(), "other")
    )

    async def get_neighbors(cid, top_k):
        return [self_hit, other]

    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_chat_payload(
                    json.dumps(
                        {
                            "has_relation": True,
                            "relation": "related_to",
                            "weight": 0.9,
                        }
                    )
                ),
            )
        )
        client = GroqClient()
        builder = MemoryGraphBuilder(client, get_neighbors)
        edges = await builder.build_edges_for(source_id)
        await client.aclose()

    assert len(edges) == 1
    assert edges[0].target_cluster_id != source_id
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_rebuild_global_fans_out() -> None:
    settings = get_settings()
    cluster_ids = [uuid4() for _ in range(3)]

    async def list_clusters():
        return cluster_ids

    async def get_neighbors(cid, top_k):
        return [
            NeighborHit(
                cosine_sim=0.9,
                source=_summary(cid, "source"),
                neighbor=_summary(uuid4(), "n"),
            )
        ]

    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_chat_payload(
                    json.dumps(
                        {
                            "has_relation": True,
                            "relation": "related_to",
                            "weight": 0.9,
                        }
                    )
                ),
            )
        )
        client = GroqClient()
        builder = MemoryGraphBuilder(client, get_neighbors, list_clusters)
        total = await builder.rebuild_global()
        await client.aclose()

    assert total == 3


@pytest.mark.asyncio
async def test_rebuild_global_requires_list_callable() -> None:
    async def get_neighbors(cid, top_k):
        return []

    client = GroqClient()
    builder = MemoryGraphBuilder(client, get_neighbors)
    with pytest.raises(RuntimeError):
        await builder.rebuild_global()
    await client.aclose()
