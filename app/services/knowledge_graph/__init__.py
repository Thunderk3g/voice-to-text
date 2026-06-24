"""Typed call knowledge-graph package.

Re-exports the pure model (C5) and the pure builder. ``build_call_graph`` /
``merge_graphs`` are lazily imported so that simply importing the model (e.g.
from the Obsidian exporter under ``TYPE_CHECKING``) does not pull in the builder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.knowledge_graph.model import (
    GraphEdge,
    GraphNode,
    NodeType,
    TypedGraph,
    node_id,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.knowledge_graph.build import build_call_graph, merge_graphs

__all__ = [
    "NodeType",
    "GraphNode",
    "GraphEdge",
    "TypedGraph",
    "node_id",
    "build_call_graph",
    "merge_graphs",
]


def __getattr__(name: str) -> Any:
    if name in ("build_call_graph", "merge_graphs"):
        from app.services.knowledge_graph import build

        return getattr(build, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
