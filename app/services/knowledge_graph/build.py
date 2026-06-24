"""Pure call knowledge-graph builder (T9).

``build_call_graph`` turns one analyzed call (a C2 ANALYSIS dict) plus optional
CDR context and an optional ``leads_canonical`` slice into a :class:`TypedGraph`.
It is **pure**: no DB / network / LLM, and it NEVER raises on out-of-range
upstream confidences — weights are clamped to ``[0, 1]`` before any edge is
constructed.

Join-phone resolution (CDR-primary, transcript fallback):

* If a ``CallContext`` is supplied, the join phone is
  ``normalize_mobile(cdr.caller_phone)`` when that is non-None, else the
  grounded transcript phone.
* Without a CDR, the join phone is the grounded transcript phone (trusted only
  when ``'phone' in lead['grounded_fields']``).

A ``phone_mismatch=True`` attr is set on the CALL node when the CDR phone and
the grounded transcript phone both normalize to *different non-None* values.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.enums import EdgeRelation
from app.services.knowledge_graph.model import (
    GraphEdge,
    GraphNode,
    NodeType,
    TypedGraph,
    node_id,
)
from app.utils.phone import clean_na, normalize_mobile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.cdr.schemas import CallContext


def _clamp(value: object) -> float:
    """Coerce ``value`` to a float in ``[0, 1]`` (defaults to 0.0 on garbage)."""

    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _grounded_lead_phone(lead: dict) -> str | None:
    """Return the normalized transcript phone iff it is grounded."""

    if "phone" in (lead.get("grounded_fields") or []):
        return normalize_mobile(lead.get("phone"))
    return None


def _normalize_product(raw: object) -> str | None:
    text = clean_na(raw)
    if text is None:
        return None
    return text.strip().lower() or None


def build_call_graph(
    analysis: dict,
    *,
    call_id: str,
    call_date: str | None = None,
    cdr: "CallContext | None" = None,
    lead_rows: list[dict] | None = None,
) -> TypedGraph:
    """Build a typed graph for a single analyzed call (see module docstring)."""

    g = TypedGraph()
    lead = analysis.get("lead") or {}

    # --- resolve join phone (CDR-primary, grounded-transcript fallback) ---- #
    grounded_phone = _grounded_lead_phone(lead)
    cdr_phone = normalize_mobile(cdr.caller_phone) if cdr is not None else None
    join_phone = cdr_phone or grounded_phone

    # phone_mismatch: CDR phone and grounded transcript phone both present and
    # normalize to different values.
    phone_mismatch = (
        cdr_phone is not None
        and grounded_phone is not None
        and cdr_phone != grounded_phone
    )

    # --- CALL node (always; the anchor) ----------------------------------- #
    call_node_id = node_id(NodeType.CALL, call_id)
    call_attrs: dict = {
        "escalation": bool(analysis.get("escalation", False)),
    }
    if call_date is not None:
        call_attrs["call_date"] = call_date
    if phone_mismatch:
        call_attrs["phone_mismatch"] = True
    g.add_node(
        GraphNode(
            id=call_node_id,
            type=NodeType.CALL,
            label=f"Call {call_id}",
            attrs=call_attrs,
        )
    )

    # --- matched leads (canonical slice) ---------------------------------- #
    matched_lead_nos: list[str] = []
    matched_products: list[str] = []
    if join_phone is not None and lead_rows:
        for row in lead_rows:
            if normalize_mobile(row.get("MOBILE_NO")) != join_phone:
                continue
            lead_no = clean_na(row.get("LEAD_NO"))
            if lead_no is not None and lead_no not in matched_lead_nos:
                matched_lead_nos.append(lead_no)
            prod = _normalize_product(row.get("PRODUCT_TYPE"))
            if prod is not None and prod not in matched_products:
                matched_products.append(prod)

    # --- LEAD node + RECEIVED_CALL ---------------------------------------- #
    if join_phone is not None:
        lead_node_id = node_id(NodeType.LEAD, join_phone)
        g.add_node(
            GraphNode(
                id=lead_node_id,
                type=NodeType.LEAD,
                label=clean_na(lead.get("full_name")) or join_phone,
                attrs={
                    "phone": join_phone,
                    "lead_nos": matched_lead_nos,
                    "matched": bool(matched_lead_nos),
                },
            )
        )
        g.add_edge(
            GraphEdge(
                src_id=lead_node_id,
                src_type=NodeType.LEAD,
                dst_id=call_node_id,
                dst_type=NodeType.CALL,
                relation=EdgeRelation.RECEIVED_CALL,
                weight=1.0,
            )
        )

    # --- DISPOSITION node + HAS_DISPOSITION ------------------------------- #
    disposition = clean_na(analysis.get("disposition"))
    if disposition is not None:
        disp_id = node_id(NodeType.DISPOSITION, disposition)
        g.add_node(
            GraphNode(
                id=disp_id,
                type=NodeType.DISPOSITION,
                label=disposition,
                attrs={},
            )
        )
        g.add_edge(
            GraphEdge(
                src_id=call_node_id,
                src_type=NodeType.CALL,
                dst_id=disp_id,
                dst_type=NodeType.DISPOSITION,
                relation=EdgeRelation.HAS_DISPOSITION,
                weight=_clamp(analysis.get("disposition_confidence")),
                reason=clean_na(analysis.get("disposition_rationale")),
            )
        )

    # --- SENTIMENT node + HAS_SENTIMENT ----------------------------------- #
    sentiment = clean_na(analysis.get("sentiment"))
    if sentiment is not None:
        sent_id = node_id(NodeType.SENTIMENT, sentiment)
        g.add_node(
            GraphNode(
                id=sent_id,
                type=NodeType.SENTIMENT,
                label=sentiment,
                attrs={},
            )
        )
        g.add_edge(
            GraphEdge(
                src_id=call_node_id,
                src_type=NodeType.CALL,
                dst_id=sent_id,
                dst_type=NodeType.SENTIMENT,
                relation=EdgeRelation.HAS_SENTIMENT,
                weight=_clamp(analysis.get("sentiment_confidence")),
            )
        )

    # --- AGENT node + HANDLED_BY (only with a real CDR agent_id) ----------- #
    agent_id = clean_na(cdr.agent_id) if cdr is not None else None
    if agent_id is not None:
        agent_node_id = node_id(NodeType.AGENT, agent_id)
        g.add_node(
            GraphNode(
                id=agent_node_id,
                type=NodeType.AGENT,
                label=agent_id,
                attrs={},
            )
        )
        g.add_edge(
            GraphEdge(
                src_id=call_node_id,
                src_type=NodeType.CALL,
                dst_id=agent_node_id,
                dst_type=NodeType.AGENT,
                relation=EdgeRelation.HANDLED_BY,
                weight=1.0,
            )
        )

    # --- CAMPAIGN node + IN_CAMPAIGN (lead->campaign; needs lead + campaign) #
    campaign = clean_na(cdr.campaign) if cdr is not None else None
    if campaign is not None:
        campaign_node_id = node_id(NodeType.CAMPAIGN, campaign)
        g.add_node(
            GraphNode(
                id=campaign_node_id,
                type=NodeType.CAMPAIGN,
                label=campaign,
                attrs={},
            )
        )
        if join_phone is not None:
            g.add_edge(
                GraphEdge(
                    src_id=node_id(NodeType.LEAD, join_phone),
                    src_type=NodeType.LEAD,
                    dst_id=campaign_node_id,
                    dst_type=NodeType.CAMPAIGN,
                    relation=EdgeRelation.IN_CAMPAIGN,
                    weight=1.0,
                )
            )

    # --- PRODUCT nodes (union: lead.product_interest + matched PRODUCT_TYPE)  #
    products: list[str] = []
    interest = _normalize_product(lead.get("product_interest"))
    if interest is not None:
        products.append(interest)
    for prod in matched_products:
        if prod not in products:
            products.append(prod)

    for prod in products:
        product_node_id = node_id(NodeType.PRODUCT, prod)
        g.add_node(
            GraphNode(
                id=product_node_id,
                type=NodeType.PRODUCT,
                label=prod,
                attrs={},
            )
        )
        # call -> product
        g.add_edge(
            GraphEdge(
                src_id=call_node_id,
                src_type=NodeType.CALL,
                dst_id=product_node_id,
                dst_type=NodeType.PRODUCT,
                relation=EdgeRelation.ABOUT_PRODUCT,
                weight=1.0,
            )
        )
        # lead -> product (only when we have a lead)
        if join_phone is not None:
            g.add_edge(
                GraphEdge(
                    src_id=node_id(NodeType.LEAD, join_phone),
                    src_type=NodeType.LEAD,
                    dst_id=product_node_id,
                    dst_type=NodeType.PRODUCT,
                    relation=EdgeRelation.INTERESTED_IN,
                    weight=1.0,
                )
            )

    return g


def merge_graphs(*graphs: TypedGraph) -> TypedGraph:
    """Merge any number of :class:`TypedGraph` into a fresh accumulator.

    Idempotent: merging the same graph twice yields the same node/edge sets as
    merging it once (dedup by node id and edge key).
    """

    merged = TypedGraph()
    for g in graphs:
        merged.merge(g)
    return merged


__all__ = ["build_call_graph", "merge_graphs"]
