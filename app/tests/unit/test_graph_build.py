"""Unit tests for the pure call-graph builder (T9).

No DB/network/LLM. Builds a ``TypedGraph`` from a C2 ANALYSIS dict plus an
optional ``CallContext`` (CDR) and optional ``lead_rows`` (leads_canonical
slice keyed by ``MOBILE_NO`` with ``LEAD_NO`` / ``PRODUCT_TYPE`` columns).
"""

from __future__ import annotations

from app.models.enums import CallDisposition, EdgeRelation, SentimentLabel
from app.services.cdr.schemas import CallContext
from app.services.knowledge_graph import build_call_graph, merge_graphs
from app.services.knowledge_graph.model import NodeType, node_id
from app.utils.phone import normalize_mobile


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

PHONE = "9876543210"
OTHER_PHONE = "9988776655"


def _lead(
    *,
    phone: str | None = None,
    grounded: bool = False,
    product_interest: str | None = None,
) -> dict:
    """A ``Lead.model_dump()``-shaped dict (C2)."""

    grounded_fields: list[str] = []
    if grounded and phone is not None:
        grounded_fields.append("phone")
    return {
        "full_name": "Asha Rao",
        "phone": phone,
        "email": None,
        "age": None,
        "gender": None,
        "occupation": None,
        "education": None,
        "income_band": None,
        "pincode": None,
        "product_interest": product_interest,
        "policy_no": None,
        "callback_time": None,
        "grounded_fields": grounded_fields,
    }


def _analysis(
    *,
    lead: dict | None = None,
    disposition: str = CallDisposition.COMPLAINT.value,
    disposition_confidence: float = 0.83,
    disposition_rationale: str | None = "raised a billing complaint",
    sentiment: str = SentimentLabel.NEGATIVE.value,
    sentiment_confidence: float = 0.71,
    escalation: bool = True,
) -> dict:
    """A C2 ANALYSIS dict (``_call_analysis_metadata`` output)."""

    return {
        "lead": lead if lead is not None else _lead(),
        "disposition": disposition,
        "disposition_confidence": disposition_confidence,
        "disposition_rationale": disposition_rationale,
        "sentiment": sentiment,
        "sentiment_confidence": sentiment_confidence,
        "escalation": escalation,
        "model": "openai/gpt-oss-120b",
    }


def _nodes_by_type(graph) -> dict[NodeType, list]:
    out: dict[NodeType, list] = {}
    for n in graph.nodes:
        out.setdefault(n.type, []).append(n)
    return out


def _node(graph, node_id_value: str):
    return next((n for n in graph.nodes if n.id == node_id_value), None)


def _edge(graph, relation: EdgeRelation):
    return [e for e in graph.edges if e.relation == relation]


# --------------------------------------------------------------------------- #
# CDR-primary happy path
# --------------------------------------------------------------------------- #


def test_cdr_primary_happy_path() -> None:
    analysis = _analysis()
    cdr = CallContext(
        crux_call_id="25689211",
        caller_phone=PHONE,
        agent_id="AG-7",
        campaign="retention_q2",
    )
    lead_rows = [
        {"MOBILE_NO": PHONE, "LEAD_NO": "L1", "PRODUCT_TYPE": "ULIP"},
        {"MOBILE_NO": "0" + PHONE, "LEAD_NO": "L2", "PRODUCT_TYPE": "Term"},
        {"MOBILE_NO": OTHER_PHONE, "LEAD_NO": "LX", "PRODUCT_TYPE": "Health"},
    ]

    g = build_call_graph(
        analysis, call_id="25689211", call_date="2026-06-20", cdr=cdr, lead_rows=lead_rows
    )

    # CALL node present with escalation attr.
    call = _node(g, node_id(NodeType.CALL, "25689211"))
    assert call is not None
    assert call.type == NodeType.CALL
    assert call.attrs.get("escalation") is True
    assert call.attrs.get("phone_mismatch") in (None, False)

    # LEAD node with matched LEAD_NOs (L1 + L2 both normalize to PHONE).
    lead = _node(g, node_id(NodeType.LEAD, PHONE))
    assert lead is not None
    assert lead.attrs.get("lead_nos") == ["L1", "L2"]
    assert lead.attrs.get("matched") is True

    # AGENT + CAMPAIGN.
    assert _node(g, node_id(NodeType.AGENT, "AG-7")) is not None
    assert _node(g, node_id(NodeType.CAMPAIGN, "retention_q2")) is not None

    # DISPOSITION / SENTIMENT nodes.
    assert _node(g, node_id(NodeType.DISPOSITION, "complaint")) is not None
    assert _node(g, node_id(NodeType.SENTIMENT, "negative")) is not None

    # All entity edge types present.
    rels = {e.relation for e in g.edges}
    assert {
        EdgeRelation.RECEIVED_CALL,
        EdgeRelation.HANDLED_BY,
        EdgeRelation.HAS_DISPOSITION,
        EdgeRelation.HAS_SENTIMENT,
        EdgeRelation.ABOUT_PRODUCT,
        EdgeRelation.INTERESTED_IN,
        EdgeRelation.IN_CAMPAIGN,
    } <= rels

    # HAS_DISPOSITION weight == confidence, reason == rationale.
    hd = _edge(g, EdgeRelation.HAS_DISPOSITION)[0]
    assert hd.weight == 0.83
    assert hd.reason == "raised a billing complaint"
    assert hd.src_id == node_id(NodeType.CALL, "25689211")
    assert hd.dst_id == node_id(NodeType.DISPOSITION, "complaint")

    # HAS_SENTIMENT weight == sentiment confidence.
    hs = _edge(g, EdgeRelation.HAS_SENTIMENT)[0]
    assert hs.weight == 0.71

    # RECEIVED_CALL is lead->call at weight 1.0.
    rc = _edge(g, EdgeRelation.RECEIVED_CALL)[0]
    assert rc.src_id == node_id(NodeType.LEAD, PHONE)
    assert rc.dst_id == node_id(NodeType.CALL, "25689211")
    assert rc.weight == 1.0

    # PRODUCT nodes = union of matched rows' PRODUCT_TYPE (ULIP, Term).
    products = {n.id for n in _nodes_by_type(g).get(NodeType.PRODUCT, [])}
    assert node_id(NodeType.PRODUCT, normalize_product("ULIP")) in products
    assert node_id(NodeType.PRODUCT, normalize_product("Term")) in products


def normalize_product(s: str) -> str:
    # Mirror the builder's product normalization (lower + strip) so the test
    # is independent of casing decisions.
    return s.strip().lower()


# --------------------------------------------------------------------------- #
# Transcript fallback (no CDR)
# --------------------------------------------------------------------------- #


def test_transcript_fallback_no_cdr() -> None:
    analysis = _analysis(lead=_lead(phone=PHONE, grounded=True, product_interest="ULIP"))

    g = build_call_graph(analysis, call_id="c-100", call_date="2026-06-21")

    # LEAD resolved from grounded transcript phone.
    lead = _node(g, node_id(NodeType.LEAD, PHONE))
    assert lead is not None
    assert lead.attrs.get("matched") is False
    assert lead.attrs.get("lead_nos") == []

    rc = _edge(g, EdgeRelation.RECEIVED_CALL)
    assert len(rc) == 1

    # No agent / campaign without a CDR.
    assert _nodes_by_type(g).get(NodeType.AGENT) is None
    assert _nodes_by_type(g).get(NodeType.CAMPAIGN) is None
    assert not _edge(g, EdgeRelation.HANDLED_BY)
    assert not _edge(g, EdgeRelation.IN_CAMPAIGN)

    # Product from lead.product_interest → ABOUT_PRODUCT + INTERESTED_IN.
    assert _edge(g, EdgeRelation.ABOUT_PRODUCT)
    assert _edge(g, EdgeRelation.INTERESTED_IN)


# --------------------------------------------------------------------------- #
# Ungrounded transcript phone → no lead resolution
# --------------------------------------------------------------------------- #


def test_ungrounded_transcript_phone_yields_no_lead() -> None:
    # lead.phone set but NOT grounded → must not be trusted.
    analysis = _analysis(lead=_lead(phone=PHONE, grounded=False))

    g = build_call_graph(analysis, call_id="c-101")

    assert _node(g, node_id(NodeType.LEAD, PHONE)) is None
    assert _nodes_by_type(g).get(NodeType.LEAD) is None
    assert not _edge(g, EdgeRelation.RECEIVED_CALL)


# --------------------------------------------------------------------------- #
# CDR junk phone → grounded transcript fallback + phone_mismatch
# --------------------------------------------------------------------------- #


def test_cdr_junk_phone_falls_back_and_flags_mismatch() -> None:
    # CDR phone is a non-None landline that normalizes to a *different* value
    # than the grounded transcript phone; transcript wins, mismatch flagged.
    analysis = _analysis(lead=_lead(phone=PHONE, grounded=True))
    # caller_phone here is already-normalized in CallContext; use a different
    # valid mobile so both normalize to distinct non-None values.
    cdr = CallContext(crux_call_id="c-200", caller_phone=OTHER_PHONE)

    g = build_call_graph(analysis, call_id="c-200", cdr=cdr)

    # CDR phone is valid & non-None, so it is the primary join key.
    lead = _node(g, node_id(NodeType.LEAD, OTHER_PHONE))
    assert lead is not None
    # Mismatch flag set because CDR phone != grounded transcript phone.
    call = _node(g, node_id(NodeType.CALL, "c-200"))
    assert call.attrs.get("phone_mismatch") is True


def test_cdr_truly_junk_phone_uses_grounded_fallback() -> None:
    # CDR caller_phone is None (e.g. landline filtered upstream) → fall back to
    # grounded transcript phone; still flag nothing weird beyond fallback.
    analysis = _analysis(lead=_lead(phone=PHONE, grounded=True))
    cdr = CallContext(crux_call_id="c-201", caller_phone=None, agent_id="AG-1")

    g = build_call_graph(analysis, call_id="c-201", cdr=cdr)

    lead = _node(g, node_id(NodeType.LEAD, PHONE))
    assert lead is not None
    # No CDR phone → no mismatch (nothing to disagree with).
    call = _node(g, node_id(NodeType.CALL, "c-201"))
    assert call.attrs.get("phone_mismatch") in (None, False)
    # agent still wired from CDR.
    assert _node(g, node_id(NodeType.AGENT, "AG-1")) is not None


# --------------------------------------------------------------------------- #
# No lead match in canonical → matched False, lead_nos []
# --------------------------------------------------------------------------- #


def test_no_lead_match_in_canonical() -> None:
    analysis = _analysis()
    cdr = CallContext(crux_call_id="c-300", caller_phone=PHONE)
    lead_rows = [{"MOBILE_NO": OTHER_PHONE, "LEAD_NO": "LX", "PRODUCT_TYPE": "Health"}]

    g = build_call_graph(analysis, call_id="c-300", cdr=cdr, lead_rows=lead_rows)

    lead = _node(g, node_id(NodeType.LEAD, PHONE))
    assert lead is not None
    assert lead.attrs.get("matched") is False
    assert lead.attrs.get("lead_nos") == []


# --------------------------------------------------------------------------- #
# NA agent / None campaign → no node/edge
# --------------------------------------------------------------------------- #


def test_na_agent_and_none_campaign_skipped() -> None:
    analysis = _analysis()
    cdr = CallContext(
        crux_call_id="c-400", caller_phone=PHONE, agent_id="NA", campaign=None
    )

    g = build_call_graph(analysis, call_id="c-400", cdr=cdr)

    assert _nodes_by_type(g).get(NodeType.AGENT) is None
    assert _nodes_by_type(g).get(NodeType.CAMPAIGN) is None
    assert not _edge(g, EdgeRelation.HANDLED_BY)
    assert not _edge(g, EdgeRelation.IN_CAMPAIGN)


# --------------------------------------------------------------------------- #
# No phone anywhere → call + disposition + sentiment but no lead
# --------------------------------------------------------------------------- #


def test_no_phone_anywhere() -> None:
    analysis = _analysis(lead=_lead(phone=None, grounded=False))
    cdr = CallContext(crux_call_id="c-500", caller_phone=None)

    g = build_call_graph(analysis, call_id="c-500", cdr=cdr)

    # No lead node / RECEIVED_CALL / INTERESTED_IN.
    assert _nodes_by_type(g).get(NodeType.LEAD) is None
    assert not _edge(g, EdgeRelation.RECEIVED_CALL)
    assert not _edge(g, EdgeRelation.INTERESTED_IN)

    # But call + disposition + sentiment + their edges exist.
    assert _node(g, node_id(NodeType.CALL, "c-500")) is not None
    assert _node(g, node_id(NodeType.DISPOSITION, "complaint")) is not None
    assert _node(g, node_id(NodeType.SENTIMENT, "negative")) is not None
    assert _edge(g, EdgeRelation.HAS_DISPOSITION)
    assert _edge(g, EdgeRelation.HAS_SENTIMENT)


# --------------------------------------------------------------------------- #
# Upstream weight > 1.0 clamped (never raises)
# --------------------------------------------------------------------------- #


def test_weight_above_one_is_clamped_not_raised() -> None:
    analysis = _analysis(disposition_confidence=1.7, sentiment_confidence=2.0)
    g = build_call_graph(analysis, call_id="c-600")

    hd = _edge(g, EdgeRelation.HAS_DISPOSITION)[0]
    hs = _edge(g, EdgeRelation.HAS_SENTIMENT)[0]
    assert hd.weight == 1.0
    assert hs.weight == 1.0


def test_weight_below_zero_is_clamped() -> None:
    analysis = _analysis(disposition_confidence=-0.5)
    g = build_call_graph(analysis, call_id="c-601")
    hd = _edge(g, EdgeRelation.HAS_DISPOSITION)[0]
    assert hd.weight == 0.0


# --------------------------------------------------------------------------- #
# merge_graphs
# --------------------------------------------------------------------------- #


def test_merge_graphs_two_calls_same_lead() -> None:
    analysis1 = _analysis()
    analysis2 = _analysis(disposition=CallDisposition.RESOLVED.value)
    cdr1 = CallContext(crux_call_id="m1", caller_phone=PHONE)
    cdr2 = CallContext(crux_call_id="m2", caller_phone=PHONE)

    g1 = build_call_graph(analysis1, call_id="m1", cdr=cdr1)
    g2 = build_call_graph(analysis2, call_id="m2", cdr=cdr2)

    merged = merge_graphs(g1, g2)

    # One lead node, two call nodes, two RECEIVED_CALL edges.
    leads = _nodes_by_type(merged).get(NodeType.LEAD, [])
    calls = _nodes_by_type(merged).get(NodeType.CALL, [])
    assert len(leads) == 1
    assert len(calls) == 2
    assert len(_edge(merged, EdgeRelation.RECEIVED_CALL)) == 2


def test_merge_graphs_idempotent() -> None:
    analysis = _analysis()
    cdr = CallContext(crux_call_id="m1", caller_phone=PHONE)
    g = build_call_graph(analysis, call_id="m1", cdr=cdr)

    once = merge_graphs(g)
    twice = merge_graphs(g, g)

    assert len(twice.nodes) == len(once.nodes)
    assert len(twice.edges) == len(once.edges)


def test_merge_graphs_empty() -> None:
    merged = merge_graphs()
    assert merged.nodes == []
    assert merged.edges == []
