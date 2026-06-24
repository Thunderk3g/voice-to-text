"""Typed knowledge-graph model (C5).

Pure, infra-free Pydantic v2 / dataclass-style models plus an in-memory
``TypedGraph`` accumulator. The Obsidian exporter consumes ``GraphNode`` /
``GraphEdge`` by field name only (``TYPE_CHECKING`` import), so the field names
here are part of the cross-component contract — do not rename without updating
``obsidian_export.py``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EdgeRelation


class NodeType(StrEnum):
    """The seven entity kinds in the call knowledge graph."""

    LEAD = "lead"
    CALL = "call"
    AGENT = "agent"
    CAMPAIGN = "campaign"
    PRODUCT = "product"
    DISPOSITION = "disposition"
    SENTIMENT = "sentiment"


def node_id(t: NodeType, raw: str) -> str:
    """Canonical stable node id: ``"<type>:<raw>"`` (e.g. ``"lead:9876543210"``)."""

    return f"{t.value}:{raw}"


class GraphNode(BaseModel):
    """An entity node. Frozen — identity is ``id`` (already type-prefixed)."""

    model_config = ConfigDict(frozen=True)

    id: str
    type: NodeType
    label: str
    attrs: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """A typed, directed, weighted edge between two nodes.

    ``weight`` is constrained to ``[0, 1]`` — upstream confidences that exceed
    this range MUST be clamped by the *builder* before constructing the edge
    (the model itself rejects out-of-range weights with a ``ValidationError``).
    """

    src_id: str
    src_type: NodeType
    dst_id: str
    dst_type: NodeType
    relation: EdgeRelation
    weight: float = Field(default=1.0, ge=0, le=1)
    reason: str | None = None

    def key(self) -> tuple[str, str, str]:
        """Dedup key: ``(src_id, dst_id, relation.value)``."""

        return (self.src_id, self.dst_id, self.relation.value)


class TypedGraph:
    """In-memory accumulator of nodes/edges with id- and key-based dedup.

    * ``add_node`` dedups by ``id``; on collision it merges ``attrs`` (new
      non-``None`` values win) and keeps the first non-empty ``label``.
    * ``add_edge`` dedups by ``edge.key()``; on collision it keeps the edge with
      the greater ``weight`` (and that edge's ``reason``).
    """

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[tuple[str, str, str], GraphEdge] = {}

    # --- nodes ------------------------------------------------------------ #
    def add_node(self, node: GraphNode) -> GraphNode:
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
            return node

        merged_attrs = {
            **existing.attrs,
            **{k: v for k, v in node.attrs.items() if v is not None},
        }
        # Keep the first non-empty label; only adopt the new one if the
        # existing label is empty.
        label = existing.label or node.label
        merged = GraphNode(
            id=existing.id,
            type=existing.type,
            label=label,
            attrs=merged_attrs,
        )
        self._nodes[node.id] = merged
        return merged

    # --- edges ------------------------------------------------------------ #
    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        key = edge.key()
        existing = self._edges.get(key)
        if existing is None or edge.weight > existing.weight:
            self._edges[key] = edge
            return edge
        return existing

    # --- merge / views ---------------------------------------------------- #
    def merge(self, other: TypedGraph) -> TypedGraph:
        for node in other.nodes:
            self.add_node(node)
        for edge in other.edges:
            self.add_edge(edge)
        return self

    @property
    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[GraphEdge]:
        return list(self._edges.values())


__all__ = ["NodeType", "GraphNode", "GraphEdge", "TypedGraph", "node_id"]
