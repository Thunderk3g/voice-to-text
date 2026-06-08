"""
Memory-graph edge inference.

For each source cluster we:
  1. Fetch its top-K nearest neighbors by centroid cosine
     (top_k = ``settings.memory_edge_top_k``).
  2. Drop neighbors whose cosine is below
     ``settings.memory_edge_min_sim``.
  3. Ask the LLM (``RELATION_INFERENCE_*``) whether each (source, neighbor)
     pair is a meaningful edge.
  4. Accept only ``has_relation=True && weight >= 0.5`` and clamp to
     ``settings.memory_edge_max_per_cluster`` edges total.

DB writes live in the persistence layer; we just return ``MemoryEdge``
records. The full-graph rebuild fans out concurrently via an
``asyncio.Semaphore(4)``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID

import structlog

from app.core.config import Settings, get_settings
from app.models.enums import EdgeRelation, Intent, Language
from app.models.schemas import MemoryEdge
from app.prompts import RELATION_INFERENCE_SYSTEM, RELATION_INFERENCE_USER_TEMPLATE
from app.services.llm.groq_client import GroqClient

logger = structlog.get_logger(__name__)


_MIN_ACCEPT_WEIGHT = 0.5
_GLOBAL_FANOUT = 4


@dataclass(frozen=True)
class ClusterSummary:
    """Lightweight cluster descriptor used in the relation prompt."""

    cluster_id: UUID
    canonical: str
    dominant_intent: Intent
    dominant_language: Language
    examples: list[str]


@dataclass(frozen=True)
class NeighborHit:
    """A nearest-neighbor cluster + its cosine similarity to the source."""

    cosine_sim: float
    source: ClusterSummary
    neighbor: ClusterSummary


# Async callables — signatures the DB layer must satisfy.
GetNeighbors = Callable[[UUID, int], Awaitable[list[NeighborHit]]]
ListClusters = Callable[[], Awaitable[list[UUID]]]


class MemoryGraphBuilder:
    """Infer memory-graph edges from cluster neighborhoods."""

    def __init__(
        self,
        client: GroqClient,
        get_cluster_neighbors_async: GetNeighbors,
        list_clusters_async: ListClusters | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._get_neighbors = get_cluster_neighbors_async
        self._list_clusters = list_clusters_async
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Single cluster
    # ------------------------------------------------------------------
    async def build_edges_for(self, cluster_id: UUID) -> list[MemoryEdge]:
        s = self._settings
        neighbors = await self._get_neighbors(cluster_id, s.memory_edge_top_k)

        candidates = [n for n in neighbors if n.cosine_sim >= s.memory_edge_min_sim]
        logger.info(
            "mem_graph.candidates",
            cluster_id=str(cluster_id),
            fetched=len(neighbors),
            after_sim_filter=len(candidates),
            min_sim=s.memory_edge_min_sim,
        )

        edges: list[MemoryEdge] = []
        now = datetime.now(timezone.utc)
        for hit in candidates:
            if hit.neighbor.cluster_id == cluster_id:
                continue
            edge = await self._infer_one(hit, now)
            if edge is not None:
                edges.append(edge)
            if len(edges) >= s.memory_edge_max_per_cluster:
                break

        logger.info(
            "mem_graph.done",
            cluster_id=str(cluster_id),
            accepted=len(edges),
        )
        return edges

    # ------------------------------------------------------------------
    # Global fan-out
    # ------------------------------------------------------------------
    async def rebuild_global(self) -> int:
        if self._list_clusters is None:
            raise RuntimeError(
                "rebuild_global requires list_clusters_async to be provided"
            )

        cluster_ids = await self._list_clusters()
        logger.info("mem_graph.rebuild_start", n_clusters=len(cluster_ids))

        sem = asyncio.Semaphore(_GLOBAL_FANOUT)

        async def _run(cid: UUID) -> list[MemoryEdge]:
            async with sem:
                try:
                    return await self.build_edges_for(cid)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "mem_graph.cluster_failed",
                        cluster_id=str(cid),
                        error=str(exc),
                    )
                    return []

        results = await asyncio.gather(*(_run(cid) for cid in cluster_ids))
        total = sum(len(r) for r in results)
        logger.info("mem_graph.rebuild_done", total_edges=total)
        return total

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _infer_one(
        self, hit: NeighborHit, now: datetime
    ) -> MemoryEdge | None:
        user_prompt = RELATION_INFERENCE_USER_TEMPLATE.format(
            a_id=str(hit.source.cluster_id),
            a_intent=hit.source.dominant_intent.value,
            a_language=hit.source.dominant_language.value,
            a_canonical=hit.source.canonical,
            a_examples="\n".join(f"  - {e}" for e in hit.source.examples) or "  (none)",
            b_id=str(hit.neighbor.cluster_id),
            b_intent=hit.neighbor.dominant_intent.value,
            b_language=hit.neighbor.dominant_language.value,
            b_canonical=hit.neighbor.canonical,
            b_examples="\n".join(f"  - {e}" for e in hit.neighbor.examples) or "  (none)",
            cosine_sim=hit.cosine_sim,
        )

        from app.core.observability import llm_calls
        from app.services.memory_graph import edge_cache

        # Cache hit shortcut: skip the LLM entirely when we already have a
        # verdict for this (src, dst) pair within the TTL window.
        cached = await edge_cache.get(
            hit.source.cluster_id, hit.neighbor.cluster_id
        )
        if cached is not None:
            payload = cached
        else:
            try:
                payload = await self._client.chat_json(
                    system=RELATION_INFERENCE_SYSTEM,
                    user=user_prompt,
                )
                llm_calls.labels(purpose="edge_label", status="ok").inc()
            except Exception as exc:  # noqa: BLE001 — retries exhausted
                llm_calls.labels(purpose="edge_label", status="error").inc()
                logger.warning(
                    "mem_graph.llm_failed",
                    source=str(hit.source.cluster_id),
                    target=str(hit.neighbor.cluster_id),
                    error=str(exc),
                )
                return None
            await edge_cache.put(
                hit.source.cluster_id, hit.neighbor.cluster_id, payload
            )

        if not bool(payload.get("has_relation")):
            return None

        try:
            weight = float(payload.get("weight", 0.0))
        except (TypeError, ValueError):
            weight = 0.0
        weight = max(0.0, min(1.0, weight))
        if weight < _MIN_ACCEPT_WEIGHT:
            return None

        relation_raw = str(payload.get("relation", "")).strip().lower()
        try:
            relation = EdgeRelation(relation_raw)
        except ValueError:
            logger.warning(
                "mem_graph.unknown_relation",
                relation=relation_raw,
                source=str(hit.source.cluster_id),
                target=str(hit.neighbor.cluster_id),
            )
            return None

        reason_raw = payload.get("reason")
        reason = str(reason_raw).strip() if reason_raw else None

        return MemoryEdge(
            source_cluster_id=hit.source.cluster_id,
            target_cluster_id=hit.neighbor.cluster_id,
            relation=relation,
            weight=weight,
            reason=reason,
            created_at=now,
        )
