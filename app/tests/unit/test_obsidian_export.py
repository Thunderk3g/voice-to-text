"""Unit tests for the Obsidian vault exporter (T10).

Pure, no DB/network/LLM. Exercises the exporter against both lightweight
duck-typed stand-ins AND the *real* ``GraphNode``/``GraphEdge`` from
``app.services.knowledge_graph.model`` (the exporter imports those under
``TYPE_CHECKING`` only, so it must work with either).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import yaml

from app.services.knowledge_graph.obsidian_export import (
    TYPE_FOLDERS,
    _flatten_attrs,
    _related,
    folder_for,
    note_name,
    render_body,
    render_frontmatter,
    render_note,
    slugify,
    write_vault,
)


# --------------------------------------------------------------------------- #
# Lightweight duck-typed stand-ins (prove no hard import of the real model).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FakeNode:
    id: str
    type: object
    label: str
    attrs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FakeEdge:
    src_id: str
    src_type: object
    dst_id: str
    dst_type: object
    relation: object
    weight: float = 1.0
    reason: str | None = None


class _Color(StrEnum):
    RED = "red"


# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #
def test_slugify_basic_punctuation_and_case() -> None:
    assert slugify("Call 2026-06: A/B") == "call-2026-06-a-b"


def test_slugify_empty_becomes_untitled() -> None:
    assert slugify("") == "untitled"
    assert slugify("   ") == "untitled"
    assert slugify("***") == "untitled"


def test_slugify_collapses_runs_and_strips_edges() -> None:
    assert slugify("A___B--C") == "a-b-c"
    assert slugify("--Hello--") == "hello"


def test_note_name_combines_type_and_id() -> None:
    assert note_name("lead", "lead:9876543210") == "lead-lead-9876543210"


# --------------------------------------------------------------------------- #
# folder_for / TYPE_FOLDERS
# --------------------------------------------------------------------------- #
def test_type_folders_cover_the_seven_node_types() -> None:
    for t in ("lead", "call", "agent", "campaign", "product",
              "disposition", "sentiment"):
        assert t in TYPE_FOLDERS


def test_folder_for_known_and_unknown() -> None:
    assert folder_for("lead") == "leads"
    assert folder_for("call") == "calls"
    # StrEnum value must resolve identically to the bare string.
    assert folder_for(_Color.RED) == "misc"
    assert folder_for("frobnicate") == "misc"


# --------------------------------------------------------------------------- #
# _flatten_attrs
# --------------------------------------------------------------------------- #
def test_flatten_attrs_strenum_to_value() -> None:
    out = _flatten_attrs({"color": _Color.RED})
    assert out["color"] == "red"
    assert isinstance(out["color"], str)


def test_flatten_attrs_list_join() -> None:
    out = _flatten_attrs({"lead_nos": ["L1", "L2", "L3"]})
    assert out["lead_nos"] == "L1, L2, L3"


def test_flatten_attrs_nested_dict_becomes_string_no_raise() -> None:
    out = _flatten_attrs({"meta": {"b": 2, "a": 1}})
    assert isinstance(out["meta"], str)
    # deterministic (sorted keys) and contains both entries
    assert '"a"' in out["meta"] and '"b"' in out["meta"]
    assert out["meta"].index('"a"') < out["meta"].index('"b"')


def test_flatten_attrs_scalars_pass_through() -> None:
    out = _flatten_attrs({"n": 3, "flag": True, "phone": "999"})
    assert out == {"n": 3, "flag": True, "phone": "999"}


# --------------------------------------------------------------------------- #
# render_frontmatter
# --------------------------------------------------------------------------- #
def _strip_fences(text: str) -> str:
    lines = text.splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    return "\n".join(lines[1:end])


def test_render_frontmatter_round_trips_with_safe_load() -> None:
    node = FakeNode(
        id="lead:9876543210",
        type="lead",
        label="Asha Verma",
        attrs={"lead_nos": ["L1", "L2"], "matched": True},
    )
    text = render_frontmatter(node)
    data = yaml.safe_load(_strip_fences(text))
    assert data == {
        "type": "lead",
        "id": "lead:9876543210",
        "label": "Asha Verma",
        "lead_nos": "L1, L2",
        "matched": True,
    }


def test_render_frontmatter_preserves_devanagari() -> None:
    node = FakeNode(id="lead:1", type="lead", label="आशा वर्मा", attrs={})
    text = render_frontmatter(node)
    assert "आशा वर्मा" in text  # allow_unicode=True, not \uXXXX escaped
    data = yaml.safe_load(_strip_fences(text))
    assert data["label"] == "आशा वर्मा"


def test_render_frontmatter_is_deterministic() -> None:
    node = FakeNode(
        id="call:1", type="call", label="Call 1",
        attrs={"z": 1, "a": 2, "m": 3},
    )
    assert render_frontmatter(node) == render_frontmatter(node)


def test_render_frontmatter_accepts_strenum_type() -> None:
    node = FakeNode(id="x:1", type=_Color.RED, label="X", attrs={})
    data = yaml.safe_load(_strip_fences(render_frontmatter(node)))
    assert data["type"] == "red"


# --------------------------------------------------------------------------- #
# _related / render_body
# --------------------------------------------------------------------------- #
def test_related_is_bidirectional_and_deduped() -> None:
    node = FakeNode(id="call:1", type="call", label="Call 1")
    edges = [
        FakeEdge("call:1", "call", "agent:7", "agent", "handled_by", 0.9),
        FakeEdge("lead:5", "lead", "call:1", "call", "received_call", 1.0),
        # duplicate of the first -> deduped
        FakeEdge("call:1", "call", "agent:7", "agent", "handled_by", 0.9),
    ]
    rel = _related(node, edges)
    # one outgoing + one incoming, duplicate dropped
    assert len(rel) == 2


def test_related_self_loop_appears_once() -> None:
    node = FakeNode(id="lead:1", type="lead", label="L")
    edges = [FakeEdge("lead:1", "lead", "lead:1", "lead", "similar_to", 0.5)]
    rel = _related(node, edges)
    assert len(rel) == 1


def test_render_body_has_heading_and_table_header() -> None:
    node = FakeNode(id="call:1", type="call", label="My Call")
    edges = [
        FakeEdge("call:1", "call", "agent:7", "agent", "handled_by", 0.9, "rationale"),
    ]
    body = render_body(node, edges)
    assert body.startswith("# My Call")
    assert "## Related" in body
    assert "| Relation | Linked Note | Weight | Reason |" in body
    # links to the other node by its note_name as a wikilink
    assert "[[" + note_name("agent", "agent:7") + "]]" in body
    assert "rationale" in body


def test_render_body_dangling_edge_still_renders_wikilink() -> None:
    # 'product:99' has no node emitted, but the edge must still render.
    node = FakeNode(id="call:1", type="call", label="Call 1")
    edges = [FakeEdge("call:1", "call", "product:99", "product", "about_product", 1.0)]
    body = render_body(node, edges)
    assert "[[" + note_name("product", "product:99") + "]]" in body


def test_render_note_combines_frontmatter_and_body() -> None:
    node = FakeNode(id="call:1", type="call", label="Call 1", attrs={"escalation": True})
    note = render_note(node, [])
    assert note.startswith("---\n")
    assert "# Call 1" in note


# --------------------------------------------------------------------------- #
# write_vault
# --------------------------------------------------------------------------- #
def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def test_write_vault_folder_layout(tmp_path) -> None:
    nodes = [
        FakeNode(id="lead:1", type="lead", label="Lead 1"),
        FakeNode(id="call:1", type="call", label="Call 1"),
        FakeNode(id="agent:1", type="agent", label="Agent 1"),
        FakeNode(id="campaign:1", type="campaign", label="Campaign 1"),
        FakeNode(id="product:1", type="product", label="Product 1"),
    ]
    paths = write_vault(nodes, [], tmp_path)
    folders = {p.parent.name for p in paths}
    assert folders == {"leads", "calls", "agents", "campaigns", "products"}
    # returns sorted paths
    assert paths == sorted(paths)


def test_write_vault_frontmatter_parses(tmp_path) -> None:
    nodes = [FakeNode(id="lead:1", type="lead", label="Asha", attrs={"n": 2})]
    paths = write_vault(nodes, [], tmp_path)
    text = _read(paths[0])
    data = yaml.safe_load(_strip_fences(text))
    assert data["id"] == "lead:1"
    assert data["n"] == 2


def test_write_vault_wikilinks_resolve_to_emitted_basenames(tmp_path) -> None:
    nodes = [
        FakeNode(id="lead:5", type="lead", label="Lead 5"),
        FakeNode(id="call:1", type="call", label="Call 1"),
    ]
    edges = [FakeEdge("lead:5", "lead", "call:1", "call", "received_call", 1.0)]
    paths = write_vault(nodes, edges, tmp_path)

    emitted_basenames = {p.stem for p in paths}
    # The lead note should wikilink to the call note's basename.
    lead_text = next(_read(p) for p in paths if p.parent.name == "leads")
    link_target = note_name("call", "call:1")
    assert "[[" + link_target + "]]" in lead_text
    assert link_target in emitted_basenames


def test_write_vault_unknown_type_goes_to_misc(tmp_path) -> None:
    nodes = [FakeNode(id="weird:1", type="weird", label="W")]
    paths = write_vault(nodes, [], tmp_path)
    assert paths[0].parent.name == "misc"


def test_write_vault_empty_creates_no_folders(tmp_path) -> None:
    paths = write_vault([], [], tmp_path)
    assert paths == []
    # no type folders created
    assert [p for p in tmp_path.iterdir()] == []


def test_write_vault_slug_collision_gets_sha_suffix(tmp_path) -> None:
    # Two distinct ids that slugify to the SAME note_name.
    nodes = [
        FakeNode(id="A/B", type="lead", label="One"),
        FakeNode(id="A:B", type="lead", label="Two"),
    ]
    paths = write_vault(nodes, [], tmp_path)
    # Distinct files, no clobbering.
    assert len(paths) == 2
    assert len({p.name for p in paths}) == 2
    assert len({_read(p) for p in paths}) == 2
    # Both still in the leads folder.
    assert {p.parent.name for p in paths} == {"leads"}


def test_write_vault_is_idempotent(tmp_path) -> None:
    nodes = [
        FakeNode(id="lead:5", type="lead", label="Lead 5"),
        FakeNode(id="call:1", type="call", label="Call 1"),
    ]
    edges = [FakeEdge("lead:5", "lead", "call:1", "call", "received_call", 1.0)]
    first = write_vault(nodes, edges, tmp_path)
    first_contents = {p: _read(p) for p in first}
    second = write_vault(nodes, edges, tmp_path)
    assert second == first
    assert {p: _read(p) for p in second} == first_contents


# --------------------------------------------------------------------------- #
# Real model integration (no TYPE_CHECKING hard import at runtime).
# --------------------------------------------------------------------------- #
def test_works_with_real_graph_model(tmp_path) -> None:
    from app.models.enums import CallDisposition, EdgeRelation
    from app.services.knowledge_graph.model import (
        GraphEdge,
        GraphNode,
        NodeType,
        node_id,
    )

    lead = GraphNode(
        id=node_id(NodeType.LEAD, "9876543210"),
        type=NodeType.LEAD,
        label="आशा वर्मा",
        attrs={"lead_nos": ["L1", "L2"], "matched": True},
    )
    call = GraphNode(
        id=node_id(NodeType.CALL, "25689211"),
        type=NodeType.CALL,
        label="Call 25689211",
        attrs={"escalation": True},
    )
    disp = GraphNode(
        id=node_id(NodeType.DISPOSITION, CallDisposition.COMPLAINT.value),
        type=NodeType.DISPOSITION,
        label="complaint",
    )
    edges = [
        GraphEdge(
            src_id=lead.id, src_type=NodeType.LEAD,
            dst_id=call.id, dst_type=NodeType.CALL,
            relation=EdgeRelation.RECEIVED_CALL, weight=1.0,
        ),
        GraphEdge(
            src_id=call.id, src_type=NodeType.CALL,
            dst_id=disp.id, dst_type=NodeType.DISPOSITION,
            relation=EdgeRelation.HAS_DISPOSITION, weight=0.87,
            reason="customer raised a grievance",
        ),
    ]
    paths = write_vault([lead, call, disp], edges, tmp_path)
    assert {p.parent.name for p in paths} == {"leads", "calls", "dispositions"}

    lead_text = next(_read(p) for p in paths if p.parent.name == "leads")
    data = yaml.safe_load(_strip_fences(lead_text))
    assert data["type"] == "lead"
    assert data["label"] == "आशा वर्मा"
    assert data["lead_nos"] == "L1, L2"
    # lead -> call wikilink present
    assert "[[" + note_name("call", call.id) + "]]" in lead_text

    call_text = next(_read(p) for p in paths if p.parent.name == "calls")
    assert "customer raised a grievance" in call_text
    assert "0.87" in call_text
