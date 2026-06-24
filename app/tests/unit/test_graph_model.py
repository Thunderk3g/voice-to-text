"""Unit tests for the typed knowledge-graph model (T7 EdgeRelation + T8 model).

Pure, no DB/network. Covers the EdgeRelation additions (C4), the GraphNode /
GraphEdge / TypedGraph contract (C5), and ``node_id`` formatting.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.enums import CallDisposition, EdgeRelation


# --------------------------------------------------------------------------- #
# T7 — EdgeRelation additions (C4)
# --------------------------------------------------------------------------- #

_NEW_ENTITY_RELATIONS = {
    "RECEIVED_CALL": "received_call",
    "HANDLED_BY": "handled_by",
    "HAS_DISPOSITION": "has_disposition",
    "HAS_SENTIMENT": "has_sentiment",
    "ABOUT_PRODUCT": "about_product",
    "INTERESTED_IN": "interested_in",
    "IN_CAMPAIGN": "in_campaign",
    "SIMILAR_TO": "similar_to",
}

_ORIGINAL_CLUSTER_RELATIONS = {
    "LEADS_TO": "leads_to",
    "RELATED_TO": "related_to",
    "SUBSET_OF": "subset_of",
    "OPPOSES": "opposes",
    "CAUSED_BY": "caused_by",
    "CO_OCCURS": "co_occurs",
}


def test_new_entity_relations_present_with_snake_case_values() -> None:
    for name, value in _NEW_ENTITY_RELATIONS.items():
        member = getattr(EdgeRelation, name)
        assert member.value == value
        # StrEnum value behaves like the string itself.
        assert member == value


def test_original_cluster_relations_untouched() -> None:
    for name, value in _ORIGINAL_CLUSTER_RELATIONS.items():
        assert getattr(EdgeRelation, name).value == value


def test_edge_relation_roundtrips_from_value() -> None:
    assert EdgeRelation("has_disposition") is EdgeRelation.HAS_DISPOSITION
    assert EdgeRelation("similar_to") is EdgeRelation.SIMILAR_TO


# --------------------------------------------------------------------------- #
# T8 — node_id formatting (C5)
# --------------------------------------------------------------------------- #


def test_node_id_format_lead() -> None:
    from app.services.knowledge_graph.model import NodeType, node_id

    assert node_id(NodeType.LEAD, "9876543210") == "lead:9876543210"


def test_node_id_format_disposition_uses_enum_value() -> None:
    from app.services.knowledge_graph.model import NodeType, node_id

    assert (
        node_id(NodeType.DISPOSITION, CallDisposition.COMPLAINT.value)
        == "disposition:complaint"
    )


# --------------------------------------------------------------------------- #
# T8 — NodeType members
# --------------------------------------------------------------------------- #


def test_node_type_members() -> None:
    from app.services.knowledge_graph.model import NodeType

    expected = {
        "LEAD": "lead",
        "CALL": "call",
        "AGENT": "agent",
        "CAMPAIGN": "campaign",
        "PRODUCT": "product",
        "DISPOSITION": "disposition",
        "SENTIMENT": "sentiment",
    }
    for name, value in expected.items():
        assert getattr(NodeType, name).value == value


# --------------------------------------------------------------------------- #
# T8 — GraphNode / GraphEdge
# --------------------------------------------------------------------------- #


def test_graph_node_is_frozen() -> None:
    from app.services.knowledge_graph.model import GraphNode, NodeType

    node = GraphNode(id="lead:9", type=NodeType.LEAD, label="Lead 9", attrs={})
    with pytest.raises(ValidationError):
        node.label = "mutated"  # type: ignore[misc]


def test_graph_edge_key() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType

    edge = GraphEdge(
        src_id="call:c1",
        src_type=NodeType.CALL,
        dst_id="disposition:complaint",
        dst_type=NodeType.DISPOSITION,
        relation=EdgeRelation.HAS_DISPOSITION,
        weight=0.9,
    )
    assert edge.key() == ("call:c1", "disposition:complaint", "has_disposition")


def test_graph_edge_weight_defaults_to_one() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType

    edge = GraphEdge(
        src_id="lead:9",
        src_type=NodeType.LEAD,
        dst_id="call:c1",
        dst_type=NodeType.CALL,
        relation=EdgeRelation.RECEIVED_CALL,
    )
    assert edge.weight == 1.0
    assert edge.reason is None


def test_graph_edge_weight_above_one_raises() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType

    with pytest.raises(ValidationError):
        GraphEdge(
            src_id="call:c1",
            src_type=NodeType.CALL,
            dst_id="disposition:complaint",
            dst_type=NodeType.DISPOSITION,
            relation=EdgeRelation.HAS_DISPOSITION,
            weight=1.5,
        )


def test_graph_edge_weight_below_zero_raises() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType

    with pytest.raises(ValidationError):
        GraphEdge(
            src_id="call:c1",
            src_type=NodeType.CALL,
            dst_id="disposition:complaint",
            dst_type=NodeType.DISPOSITION,
            relation=EdgeRelation.HAS_DISPOSITION,
            weight=-0.1,
        )


# --------------------------------------------------------------------------- #
# T8 — TypedGraph add_node dedup / label / attr-merge
# --------------------------------------------------------------------------- #


def test_add_node_dedup_merges_attrs_and_keeps_first_label() -> None:
    from app.services.knowledge_graph.model import GraphNode, NodeType, TypedGraph

    g = TypedGraph()
    g.add_node(
        GraphNode(id="lead:9", type=NodeType.LEAD, label="First", attrs={"a": 1})
    )
    g.add_node(
        GraphNode(
            id="lead:9",
            type=NodeType.LEAD,
            label="Second",
            attrs={"b": 2, "a": None},
        )
    )

    assert len(g.nodes) == 1
    node = g.nodes[0]
    # Label: keep first non-empty.
    assert node.label == "First"
    # Attrs merged; None values from the new node must not clobber existing keys.
    assert node.attrs == {"a": 1, "b": 2}


def test_add_node_first_empty_label_is_replaced() -> None:
    from app.services.knowledge_graph.model import GraphNode, NodeType, TypedGraph

    g = TypedGraph()
    g.add_node(GraphNode(id="lead:9", type=NodeType.LEAD, label="", attrs={}))
    g.add_node(GraphNode(id="lead:9", type=NodeType.LEAD, label="Real", attrs={}))

    assert len(g.nodes) == 1
    assert g.nodes[0].label == "Real"


# --------------------------------------------------------------------------- #
# T8 — TypedGraph add_edge dedup (higher weight wins)
# --------------------------------------------------------------------------- #


def test_add_edge_dedup_higher_weight_wins() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType, TypedGraph

    g = TypedGraph()
    low = GraphEdge(
        src_id="call:c1",
        src_type=NodeType.CALL,
        dst_id="disposition:complaint",
        dst_type=NodeType.DISPOSITION,
        relation=EdgeRelation.HAS_DISPOSITION,
        weight=0.4,
        reason="low",
    )
    high = GraphEdge(
        src_id="call:c1",
        src_type=NodeType.CALL,
        dst_id="disposition:complaint",
        dst_type=NodeType.DISPOSITION,
        relation=EdgeRelation.HAS_DISPOSITION,
        weight=0.9,
        reason="high",
    )
    g.add_edge(low)
    g.add_edge(high)

    assert len(g.edges) == 1
    assert g.edges[0].weight == pytest.approx(0.9)
    assert g.edges[0].reason == "high"


def test_add_edge_dedup_lower_weight_ignored() -> None:
    from app.services.knowledge_graph.model import GraphEdge, NodeType, TypedGraph

    g = TypedGraph()
    high = GraphEdge(
        src_id="call:c1",
        src_type=NodeType.CALL,
        dst_id="disposition:complaint",
        dst_type=NodeType.DISPOSITION,
        relation=EdgeRelation.HAS_DISPOSITION,
        weight=0.9,
        reason="high",
    )
    low = GraphEdge(
        src_id="call:c1",
        src_type=NodeType.CALL,
        dst_id="disposition:complaint",
        dst_type=NodeType.DISPOSITION,
        relation=EdgeRelation.HAS_DISPOSITION,
        weight=0.4,
        reason="low",
    )
    g.add_edge(high)
    g.add_edge(low)

    assert len(g.edges) == 1
    assert g.edges[0].weight == pytest.approx(0.9)
    assert g.edges[0].reason == "high"


# --------------------------------------------------------------------------- #
# T8 — TypedGraph.merge
# --------------------------------------------------------------------------- #


def test_merge_combines_and_dedups() -> None:
    from app.services.knowledge_graph.model import (
        GraphEdge,
        GraphNode,
        NodeType,
        TypedGraph,
    )

    a = TypedGraph()
    a.add_node(GraphNode(id="lead:9", type=NodeType.LEAD, label="L", attrs={"x": 1}))
    a.add_edge(
        GraphEdge(
            src_id="lead:9",
            src_type=NodeType.LEAD,
            dst_id="call:c1",
            dst_type=NodeType.CALL,
            relation=EdgeRelation.RECEIVED_CALL,
            weight=0.5,
        )
    )

    b = TypedGraph()
    b.add_node(GraphNode(id="lead:9", type=NodeType.LEAD, label="L", attrs={"y": 2}))
    b.add_node(GraphNode(id="call:c2", type=NodeType.CALL, label="C2", attrs={}))
    b.add_edge(
        GraphEdge(
            src_id="lead:9",
            src_type=NodeType.LEAD,
            dst_id="call:c1",
            dst_type=NodeType.CALL,
            relation=EdgeRelation.RECEIVED_CALL,
            weight=0.9,
        )
    )

    a.merge(b)

    ids = {n.id for n in a.nodes}
    assert ids == {"lead:9", "call:c2"}
    lead = next(n for n in a.nodes if n.id == "lead:9")
    assert lead.attrs == {"x": 1, "y": 2}
    # Edge deduped, higher weight from b wins.
    assert len(a.edges) == 1
    assert a.edges[0].weight == pytest.approx(0.9)
