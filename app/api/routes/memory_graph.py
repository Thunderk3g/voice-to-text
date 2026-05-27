"""
GET /memory-graph — filtered MemoryGraph for the Cytoscape view.

Returns only edges with weight >= `min_weight`, then their incident
nodes (clusters). Hard cap on edges to keep payloads bounded.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.models.schemas import ClusterRecord, MemoryEdge, MemoryGraph

router = APIRouter(tags=["memory-graph"])


@router.get("/memory-graph", response_model=MemoryGraph)
async def get_memory_graph(
    min_weight: float = Query(default=0.5, ge=0.0, le=1.0),
    limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
) -> MemoryGraph:
    from app.db.models import MemoryEdge as MemoryEdgeORM, SemanticCluster

    edge_stmt = (
        select(MemoryEdgeORM)
        .where(MemoryEdgeORM.weight >= min_weight)
        .order_by(MemoryEdgeORM.weight.desc())
        .limit(limit)
    )
    edge_rows = (await db.execute(edge_stmt)).scalars().all()
    edges = [MemoryEdge.model_validate(e) for e in edge_rows]

    cluster_ids: set = set()
    for edge in edges:
        cluster_ids.add(edge.source_cluster_id)
        cluster_ids.add(edge.target_cluster_id)

    nodes: list[ClusterRecord] = []
    if cluster_ids:
        node_stmt = select(SemanticCluster).where(SemanticCluster.id.in_(cluster_ids))
        node_rows = (await db.execute(node_stmt)).scalars().all()
        nodes = [ClusterRecord.model_validate(n) for n in node_rows]

    return MemoryGraph(nodes=nodes, edges=edges)
