"""
GET /knowledge-graph — the typed entity graph (leads / calls / agents / campaigns
/ products / dispositions / sentiments) for the Cytoscape view and Obsidian export.

The graph is assembled from analyzed calls (``calls.metadata.analysis``) via the
pipeline orchestrator. Edges are emitted Cytoscape-style (``source``/``target``).
The graph source is a dependency so it can be overridden in tests without a DB.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.knowledge_graph import TypedGraph
from app.services.pipeline import AnalyzedCall, build_artifacts

router = APIRouter(tags=["knowledge-graph"])


class KGNode(BaseModel):
    id: str
    type: str
    label: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class KGEdge(BaseModel):
    source: str
    target: str
    relation: str
    weight: float
    reason: str | None = None


class KnowledgeGraph(BaseModel):
    nodes: list[KGNode]
    edges: list[KGEdge]


async def _build_graph_from_db(db: AsyncSession, limit: int) -> TypedGraph:
    """Assemble the typed graph from calls that already carry an analysis blob."""
    rows = (
        await db.execute(
            text(
                "SELECT id, metadata->'analysis' AS analysis, "
                "to_char(created_at, 'YYYY-MM-DD') AS d "
                "FROM calls WHERE metadata ? 'analysis' "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        )
    ).mappings().all()

    calls: list[AnalyzedCall] = []
    for r in rows:
        analysis = r["analysis"]
        if not analysis:
            continue
        calls.append(
            AnalyzedCall(
                call_id=str(r["id"]),
                call_date=r["d"] or "",
                analysis=dict(analysis),
            )
        )
    return build_artifacts(calls).graph


async def get_knowledge_graph(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> TypedGraph:
    """Default graph provider — reads analyzed calls from the DB. Overridable in tests."""
    return await _build_graph_from_db(db, limit)


def serialize_graph(graph: TypedGraph) -> KnowledgeGraph:
    nodes = [
        KGNode(id=n.id, type=str(n.type), label=n.label, attrs=dict(n.attrs))
        for n in graph.nodes
    ]
    edges = [
        KGEdge(
            source=e.src_id,
            target=e.dst_id,
            relation=str(e.relation),
            weight=float(e.weight),
            reason=e.reason,
        )
        for e in graph.edges
    ]
    return KnowledgeGraph(nodes=nodes, edges=edges)


@router.get("/knowledge-graph", response_model=KnowledgeGraph)
async def knowledge_graph(
    graph: TypedGraph = Depends(get_knowledge_graph),
) -> KnowledgeGraph:
    return serialize_graph(graph)
