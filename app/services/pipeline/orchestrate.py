"""End-to-end orchestration: analyzed calls -> {call_summary.csv, typed graph, Obsidian vault}.

This is the connective tissue between the per-call analyzer output (the C2
ANALYSIS dict persisted at ``calls.metadata.analysis``) and the three Phase-2
artifacts:

  * a per-mobile ``call_summary`` (additive ``CALL_*`` rows for the CI enrichment
    join), via ``app.services.enrichment.call_summary``;
  * a merged typed knowledge graph, via ``app.services.knowledge_graph``;
  * an Obsidian markdown vault, via ``app.services.knowledge_graph.obsidian_export``.

Pure and side-effect-free except ``export_artifacts`` (writes files). The graph
is returned in-memory so the API layer can serve it without re-deriving.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.enrichment.call_summary import (
    CallRecord,
    build_call_summary,
    write_call_summary,
)
from app.services.knowledge_graph import TypedGraph, build_call_graph, merge_graphs
from app.services.knowledge_graph.obsidian_export import write_vault


@dataclass
class AnalyzedCall:
    """One analyzed call ready for artifact assembly.

    ``analysis`` is the C2 ANALYSIS dict (``app.workers.tasks._call_analysis_metadata``
    output). ``cdr`` is an optional resolved ``CallContext`` (carries the
    deterministic caller phone + agent/campaign). ``lead_rows`` are the matched
    ``leads_canonical`` rows (dicts with ``MOBILE_NO``/``LEAD_NO``/``PRODUCT_TYPE``).
    """

    call_id: str
    call_date: str  # ISO YYYY-MM-DD, or '' when unknown
    analysis: dict
    cdr: Any | None = None  # app.services.cdr.schemas.CallContext | None
    lead_rows: list[dict] | None = None


@dataclass
class Artifacts:
    call_summary_rows: list[dict]
    graph: TypedGraph


def build_artifacts(calls: list[AnalyzedCall]) -> Artifacts:
    """Assemble the per-mobile call_summary rows and the merged typed graph."""
    records = [
        CallRecord(
            call_id=c.call_id,
            call_date=c.call_date,
            phone=getattr(c.cdr, "caller_phone", None),
            analysis=c.analysis,
        )
        for c in calls
    ]
    summary_rows = build_call_summary(records)

    graphs = [
        build_call_graph(
            c.analysis,
            call_id=c.call_id,
            call_date=c.call_date,
            cdr=c.cdr,
            lead_rows=c.lead_rows,
        )
        for c in calls
    ]
    graph = merge_graphs(*graphs) if graphs else TypedGraph()
    return Artifacts(call_summary_rows=summary_rows, graph=graph)


def export_artifacts(artifacts: Artifacts, out_dir: str | Path) -> dict:
    """Write call_summary.csv + the Obsidian vault under ``out_dir``; return stats."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary_path = out / "call_summary.csv"
    write_call_summary(artifacts.call_summary_rows, str(summary_path))

    vault_dir = out / "obsidian_vault"
    note_paths = write_vault(artifacts.graph.nodes, artifacts.graph.edges, vault_dir)

    return {
        "call_summary_path": str(summary_path),
        "call_summary_rows": len(artifacts.call_summary_rows),
        "vault_dir": str(vault_dir),
        "vault_notes": len(note_paths),
        "graph_nodes": len(artifacts.graph.nodes),
        "graph_edges": len(artifacts.graph.edges),
    }


def run(calls: list[AnalyzedCall], out_dir: str | Path) -> dict:
    """Convenience: build then export all artifacts in one call."""
    return export_artifacts(build_artifacts(calls), out_dir)


__all__ = ["AnalyzedCall", "Artifacts", "build_artifacts", "export_artifacts", "run"]
