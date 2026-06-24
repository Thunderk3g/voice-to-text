"""End-to-end orchestration test on MOCK data (no network/DB/LLM/STT).

Feeds analyzed-call records (the C2 ANALYSIS dict shape) through the
orchestrator and asserts the three artifacts compose correctly:
call_summary.csv (additive CALL_* rows), the merged typed graph, and the
Obsidian vault.
"""
from __future__ import annotations

import pandas as pd

from app.services.cdr.schemas import CallContext
from app.services.pipeline import AnalyzedCall, run, build_artifacts


def _lead(**over) -> dict:
    base = {
        "full_name": None, "phone": None, "email": None, "age": None,
        "gender": None, "occupation": None, "education": None, "income_band": None,
        "pincode": None, "product_interest": None, "policy_no": None,
        "callback_time": None, "grounded_fields": [],
    }
    base.update(over)
    return base


def _analysis(disposition, sentiment, escalation, lead) -> dict:
    return {
        "lead": lead,
        "disposition": disposition, "disposition_confidence": 0.8,
        "disposition_rationale": f"rationale for {disposition}",
        "sentiment": sentiment, "sentiment_confidence": 0.7,
        "escalation": escalation, "model": "openai/gpt-oss-120b",
    }


def _calls() -> list[AnalyzedCall]:
    leads_9876 = [
        {"MOBILE_NO": "9876543210", "LEAD_NO": "L1", "PRODUCT_TYPE": "Term"},
        {"MOBILE_NO": "9876543210", "LEAD_NO": "L2", "PRODUCT_TYPE": "ULIP"},
    ]
    cdr_a = CallContext(crux_call_id="25689211", caller_phone="9876543210",
                        agent_id="AG1", campaign="Camp-Term")
    cdr_b = CallContext(crux_call_id="25689212", caller_phone="9876543210",
                        agent_id="AG2", campaign="Camp-Term")
    return [
        # Two calls on the SAME mobile -> collapse to one summary row.
        AnalyzedCall(
            call_id="25689211", call_date="2026-01-05",
            analysis=_analysis("resolved", "positive", False,
                               _lead(full_name="Asha Rao", product_interest="term")),
            cdr=cdr_a, lead_rows=leads_9876,
        ),
        AnalyzedCall(
            call_id="25689212", call_date="2026-02-10",
            analysis=_analysis("callback_requested", "negative", True,
                               _lead(full_name="Asha Rao")),
            cdr=cdr_b, lead_rows=leads_9876,
        ),
        # A different mobile, NO CDR -> transcript-grounded phone path, no lead match.
        AnalyzedCall(
            call_id="30319304", call_date="2026-01-20",
            analysis=_analysis("not_interested", "neutral", False,
                               _lead(phone="9123456789", grounded_fields=["phone"])),
            cdr=None, lead_rows=[],
        ),
    ]


def test_orchestrator_builds_all_three_artifacts(tmp_path):
    stats = run(_calls(), tmp_path)

    # Two distinct mobiles -> two call_summary rows.
    assert stats["call_summary_rows"] == 2
    assert stats["vault_notes"] == stats["graph_nodes"]
    assert stats["graph_edges"] > 0

    # --- call_summary.csv: additive CALL_* row, winning-disposition collapse ---
    df = pd.read_csv(stats["call_summary_path"], dtype=str, keep_default_na=False)
    assert len(df) == 2
    row = df[df["PHONE_NUMBER"] == "9876543210"].iloc[0]
    # callback_requested is more significant than resolved -> it wins the collapse.
    assert row["CALL_DISPOSITION"] == "callback_requested"
    assert row["CALL_ESCALATION"] == "true"        # logical OR across the two calls
    assert row["CALL_N_CALLS"] == "2"
    assert row["CALL_LEAD_NAME"] == "Asha Rao"


def test_graph_has_typed_nodes_and_lead_match(tmp_path):
    arts = build_artifacts(_calls())
    ids = {n.id: n for n in arts.graph.nodes}
    types = {str(n.type) for n in arts.graph.nodes}

    # heterogeneous node types present
    assert {"lead", "call", "agent", "campaign", "disposition", "sentiment"} <= types

    # the CDR-matched lead carries its LEAD_NOs
    lead = ids["lead:9876543210"]
    assert lead.attrs.get("matched") is True
    assert set(lead.attrs.get("lead_nos", [])) == {"L1", "L2"}

    # the no-CDR call still produced a transcript-grounded lead node
    assert "lead:9123456789" in ids
    assert ids["lead:9123456789"].attrs.get("matched") is False

    # at least one RECEIVED_CALL edge (lead -> call)
    rels = {e.relation.value for e in arts.graph.edges}
    assert "received_call" in rels
    assert "has_disposition" in rels


def test_obsidian_vault_layout_and_wikilinks(tmp_path):
    stats = run(_calls(), tmp_path)
    vault = tmp_path / "obsidian_vault"
    assert (vault / "leads").is_dir()
    assert (vault / "calls").is_dir()
    # the matched lead's note (filename derives from node id 'lead:9876543210')
    lead_notes = [p for p in (vault / "leads").glob("*.md") if "9876543210" in p.name]
    assert lead_notes, list((vault / "leads").glob("*.md"))
    text = lead_notes[0].read_text(encoding="utf-8")
    assert "[[" in text and "---" in text  # wikilinks + YAML frontmatter
