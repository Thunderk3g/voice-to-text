"""Pure assembly + rendering of the Phase A findings artifact.

Takes ``ClusterObservation`` records (gathered by the script from the DB) and
produces a findings dict + a Markdown report. No I/O here so it is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.diagnostics.cluster_metrics import (
    intent_purity,
    normalized_entropy,
    size_stats,
)


@dataclass(frozen=True)
class ClusterObservation:
    """Everything the diagnostics need about one cluster."""

    cluster_id: str
    label: str | None
    canonical_question: str | None
    frequency: int
    size: int                       # actual member count pulled
    member_intents: list[str] = field(default_factory=list)
    member_glosses: list[str] = field(default_factory=list)
    dispersion: float | None = None  # mean cosine distance to centroid


def flag_coarse(
    obs: ClusterObservation,
    *,
    size_threshold: int,
    purity_threshold: float,
) -> bool:
    """A cluster is 'coarse' (distinct issues merged) when it is both large and
    intent-impure: size >= threshold AND intent_purity < threshold.
    """
    return (
        obs.size >= size_threshold
        and intent_purity(obs.member_intents) < purity_threshold
    )


def _cluster_row(obs: ClusterObservation) -> dict:
    return {
        "cluster_id": obs.cluster_id,
        "label": obs.label,
        "canonical_question": obs.canonical_question,
        "frequency": obs.frequency,
        "size": obs.size,
        "intent_purity": round(intent_purity(obs.member_intents), 3),
        "intent_entropy": round(normalized_entropy(obs.member_intents), 3),
        "dispersion": round(obs.dispersion, 3) if obs.dispersion is not None else None,
        "sample_glosses": obs.member_glosses[:5],
    }


def assemble_findings(
    observations: list[ClusterObservation],
    *,
    qtype_distribution: dict[str, int],
    intent_distribution: dict[str, int],
    size_threshold: int = 10,
    purity_threshold: float = 0.6,
) -> dict:
    """Build the structured findings dict from per-cluster observations."""
    coarse = [
        o for o in observations
        if flag_coarse(o, size_threshold=size_threshold, purity_threshold=purity_threshold)
    ]
    coarse_sorted = sorted(coarse, key=lambda o: o.size, reverse=True)
    return {
        "n_clusters": len(observations),
        "n_coarse": len(coarse),
        "size_threshold": size_threshold,
        "purity_threshold": purity_threshold,
        "size_stats": size_stats([o.size for o in observations]),
        "qtype_distribution": dict(qtype_distribution),
        "intent_distribution": dict(intent_distribution),
        "coarse_clusters": [_cluster_row(o) for o in coarse_sorted],
        "all_clusters": [_cluster_row(o) for o in observations],
    }


def _render_dist(dist: dict[str, int]) -> str:
    if not dist:
        return "_(none)_"
    rows = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    return "\n".join(f"- `{k}`: {v}" for k, v in rows)


def render_markdown(findings: dict) -> str:
    """Render the findings dict as a human-readable Markdown report."""
    ss = findings["size_stats"]
    lines: list[str] = []
    lines.append("# Phase A — Findings: Clustering & Extraction Granularity")
    lines.append("")
    lines.append(
        f"**Clusters analysed:** {findings['n_clusters']} | "
        f"**Coarse clusters:** {findings['n_coarse']} "
        f"(size ≥ {findings['size_threshold']} AND intent purity < {findings['purity_threshold']})"
    )
    lines.append("")
    lines.append("## Cluster size distribution")
    lines.append(
        f"count={ss['count']}, min={ss['min']}, median={ss['median']}, "
        f"mean={ss['mean']}, p90={ss['p90']}, max={ss['max']}"
    )
    lines.append("")
    lines.append("## Question vs. complaint (QuestionType distribution)")
    lines.append(_render_dist(findings["qtype_distribution"]))
    lines.append("")
    lines.append("## Intent distribution")
    lines.append(_render_dist(findings["intent_distribution"]))
    lines.append("")
    lines.append("## Coarse clusters (distinct issues likely merged)")
    if not findings["coarse_clusters"]:
        lines.append("_No coarse clusters detected at the current thresholds._")
    else:
        lines.append("| cluster_id | size | purity | entropy | dispersion | canonical |")
        lines.append("|---|---|---|---|---|---|")
        for c in findings["coarse_clusters"]:
            lines.append(
                f"| {c['cluster_id']} | {c['size']} | {c['intent_purity']} | "
                f"{c['intent_entropy']} | {c['dispersion']} | "
                f"{(c['canonical_question'] or '')[:60]} |"
            )
        lines.append("")
        lines.append("### Sample members of the worst offenders")
        for c in findings["coarse_clusters"][:5]:
            lines.append(f"- **{c['cluster_id']}** ({c['size']} members, purity {c['intent_purity']}):")
            for g in c["sample_glosses"]:
                lines.append(f"  - {g}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ClusterObservation",
    "flag_coarse",
    "assemble_findings",
    "render_markdown",
]
