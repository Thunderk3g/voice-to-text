# Phase A — Clustering & Extraction Granularity Diagnostics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible diagnostics tool that measures *where* the current clustering/extraction is "too coarse" — quantifying cluster size, intra-cluster intent impurity, and embedding dispersion — and emits a findings note that defines the concrete taxonomy/clustering changes for Phases B and C.

**Architecture:** Pure, unit-tested metric functions (`app/diagnostics/cluster_metrics.py`) + a pure findings assembler/renderer (`app/diagnostics/report.py`), driven by a thin async DB-pull script (`app/scripts/phase_a_diagnostics.py`) that reads the *already-populated* Postgres and writes a Markdown + JSON findings artifact. No schema changes, no migrations — read-only diagnosis.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 async, pgvector, numpy, pytest, structlog. Mirrors existing patterns in `app/scripts/` and `app/db/repositories.py`.

**Scope note:** This plan covers **Phase A only**. Phases B (targeted extension + clustering tuning) and C (discrepancy dimension) are deliberately deferred — their exact shape is an *output* of this plan's findings note. See `docs/superpowers/specs/2026-06-08-issue-discrepancy-analytics-design.md`.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/diagnostics/__init__.py` | Package marker (new package). |
| `app/diagnostics/cluster_metrics.py` | Pure metric functions: normalized entropy, intent purity, size stats, centroid dispersion. No I/O. |
| `app/diagnostics/report.py` | Pure: `ClusterObservation` dataclass, `flag_coarse`, `assemble_findings`, `render_markdown`. No I/O. |
| `app/scripts/phase_a_diagnostics.py` | Thin async script: pull observations from Postgres, call the pure functions, write the findings artifact. |
| `app/tests/unit/test_cluster_metrics.py` | Unit tests for `cluster_metrics.py`. |
| `app/tests/unit/test_diagnostics_report.py` | Unit tests for `report.py` (assembly + rendering + coarse-flagging) on synthetic data. |
| `docs/superpowers/diagnostics/2026-06-08-phase-a-findings.md` | **Output artifact** (written by the script in Task 5, not by hand). |

---

## Task 1: Diagnostics package + pure metric functions

**Files:**
- Create: `app/diagnostics/__init__.py`
- Create: `app/diagnostics/cluster_metrics.py`
- Test: `app/tests/unit/test_cluster_metrics.py`

- [ ] **Step 1: Create the package marker**

Create `app/diagnostics/__init__.py`:

```python
"""Read-only diagnostics for clustering/extraction granularity (Phase A)."""
```

- [ ] **Step 2: Write the failing tests**

Create `app/tests/unit/test_cluster_metrics.py`:

```python
import math

import pytest

from app.diagnostics.cluster_metrics import (
    intent_purity,
    mean_cosine_distance_to_centroid,
    normalized_entropy,
    size_stats,
)


def test_normalized_entropy_pure_cluster_is_zero():
    assert normalized_entropy(["a", "a", "a"]) == 0.0


def test_normalized_entropy_empty_is_zero():
    assert normalized_entropy([]) == 0.0


def test_normalized_entropy_uniform_two_labels_is_one():
    assert normalized_entropy(["a", "b"]) == pytest.approx(1.0)


def test_normalized_entropy_uniform_four_labels_is_one():
    assert normalized_entropy(["a", "b", "c", "d"]) == pytest.approx(1.0)


def test_normalized_entropy_skewed_is_between_zero_and_one():
    h = normalized_entropy(["a", "a", "a", "b"])
    assert 0.0 < h < 1.0


def test_intent_purity_pure_is_one():
    assert intent_purity(["x", "x", "x"]) == 1.0


def test_intent_purity_half_is_half():
    assert intent_purity(["x", "x", "y", "y"]) == pytest.approx(0.5)


def test_intent_purity_empty_is_one():
    assert intent_purity([]) == 1.0


def test_size_stats_basic():
    stats = size_stats([1, 2, 3, 4, 100])
    assert stats["count"] == 5
    assert stats["max"] == 100
    assert stats["min"] == 1
    assert stats["median"] == 3


def test_size_stats_empty():
    stats = size_stats([])
    assert stats["count"] == 0
    assert stats["max"] == 0


def test_mean_cosine_distance_identical_vectors_is_zero():
    vecs = [[1.0, 0.0], [1.0, 0.0]]
    centroid = [1.0, 0.0]
    assert mean_cosine_distance_to_centroid(vecs, centroid) == pytest.approx(0.0, abs=1e-6)


def test_mean_cosine_distance_orthogonal_is_one():
    vecs = [[0.0, 1.0]]
    centroid = [1.0, 0.0]
    assert mean_cosine_distance_to_centroid(vecs, centroid) == pytest.approx(1.0, abs=1e-6)


def test_mean_cosine_distance_empty_is_zero():
    assert mean_cosine_distance_to_centroid([], [1.0, 0.0]) == 0.0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest app/tests/unit/test_cluster_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.diagnostics.cluster_metrics'`

- [ ] **Step 4: Write the implementation**

Create `app/diagnostics/cluster_metrics.py`:

```python
"""Pure metric functions for clustering granularity diagnostics.

No I/O, no DB — every function takes plain Python data so it is trivially
unit-testable. Used by ``app/scripts/phase_a_diagnostics.py``.
"""

from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Sequence

import numpy as np


def normalized_entropy(labels: Sequence[str]) -> float:
    """Shannon entropy of the label distribution, normalized to [0, 1].

    0.0 = all one label (pure); 1.0 = uniform across the distinct labels seen.
    Empty or single-label input returns 0.0.
    """
    n = len(labels)
    if n == 0:
        return 0.0
    counts = Counter(labels)
    if len(counts) <= 1:
        return 0.0
    h = -sum((c / n) * math.log2(c / n) for c in counts.values())
    return h / math.log2(len(counts))


def intent_purity(labels: Sequence[str]) -> float:
    """Fraction of items belonging to the single most common label.

    1.0 = pure; lower = more mixed. Empty input returns 1.0 (vacuously pure).
    """
    n = len(labels)
    if n == 0:
        return 1.0
    counts = Counter(labels)
    return max(counts.values()) / n


def size_stats(sizes: Sequence[int]) -> dict[str, float]:
    """Summary statistics over a list of cluster sizes."""
    if not sizes:
        return {"count": 0, "min": 0, "max": 0, "mean": 0.0, "median": 0.0, "p90": 0}
    ordered = sorted(sizes)
    p90_idx = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 2),
        "median": median(ordered),
        "p90": ordered[p90_idx],
    }


def mean_cosine_distance_to_centroid(
    vectors: Sequence[Sequence[float]],
    centroid: Sequence[float],
) -> float:
    """Mean cosine distance (1 - cosine similarity) of members to the centroid.

    Higher = members are more spread out = the cluster is internally diverse.
    Empty ``vectors`` returns 0.0.
    """
    if len(vectors) == 0:
        return 0.0
    mat = np.asarray(vectors, dtype=np.float64)
    c = np.asarray(centroid, dtype=np.float64)
    c_norm = np.linalg.norm(c)
    if c_norm == 0:
        return 0.0
    row_norms = np.linalg.norm(mat, axis=1)
    row_norms[row_norms == 0] = 1.0
    cos_sim = (mat @ c) / (row_norms * c_norm)
    cos_sim = np.clip(cos_sim, -1.0, 1.0)
    return float(np.mean(1.0 - cos_sim))


__all__ = [
    "normalized_entropy",
    "intent_purity",
    "size_stats",
    "mean_cosine_distance_to_centroid",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest app/tests/unit/test_cluster_metrics.py -v`
Expected: PASS (13 passed)

- [ ] **Step 6: Commit**

```bash
git add app/diagnostics/__init__.py app/diagnostics/cluster_metrics.py app/tests/unit/test_cluster_metrics.py
git commit -m "feat(diagnostics): pure cluster-granularity metric functions"
```

---

## Task 2: Findings assembler + renderer

**Files:**
- Create: `app/diagnostics/report.py`
- Test: `app/tests/unit/test_diagnostics_report.py`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/unit/test_diagnostics_report.py`:

```python
from app.diagnostics.report import (
    ClusterObservation,
    assemble_findings,
    flag_coarse,
    render_markdown,
)


def _obs(cluster_id, size, intents, dispersion=0.1):
    return ClusterObservation(
        cluster_id=cluster_id,
        label=f"label-{cluster_id}",
        canonical_question=f"q-{cluster_id}",
        frequency=size,
        size=size,
        member_intents=list(intents),
        member_glosses=[f"gloss-{i}" for i in range(min(size, 3))],
        dispersion=dispersion,
    )


def test_flag_coarse_large_and_impure_is_true():
    obs = _obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10)
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is True


def test_flag_coarse_large_but_pure_is_false():
    obs = _obs("c2", size=20, intents=["a"] * 20)
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is False


def test_flag_coarse_small_and_impure_is_false():
    obs = _obs("c3", size=4, intents=["a", "b", "c", "d"])
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is False


def test_assemble_findings_counts_coarse_clusters():
    observations = [
        _obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10),   # coarse
        _obs("c2", size=20, intents=["a"] * 20),                # pure
        _obs("c3", size=4, intents=["a", "b", "c", "d"]),       # small
    ]
    findings = assemble_findings(
        observations,
        qtype_distribution={"question": 40, "complaint": 4},
        intent_distribution={"premium_payment": 30, "claim_process": 14},
        size_threshold=10,
        purity_threshold=0.6,
    )
    assert findings["n_clusters"] == 3
    assert findings["n_coarse"] == 1
    assert findings["coarse_clusters"][0]["cluster_id"] == "c1"
    assert findings["qtype_distribution"]["complaint"] == 4


def test_render_markdown_contains_key_sections():
    observations = [_obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10)]
    findings = assemble_findings(
        observations,
        qtype_distribution={"question": 10, "complaint": 10},
        intent_distribution={"premium_payment": 20},
        size_threshold=10,
        purity_threshold=0.6,
    )
    md = render_markdown(findings)
    assert "# Phase A — Findings" in md
    assert "Coarse clusters" in md
    assert "c1" in md
    assert "Question vs. complaint" in md
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest app/tests/unit/test_diagnostics_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.diagnostics.report'`

- [ ] **Step 3: Write the implementation**

Create `app/diagnostics/report.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest app/tests/unit/test_diagnostics_report.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/diagnostics/report.py app/tests/unit/test_diagnostics_report.py
git commit -m "feat(diagnostics): findings assembler + markdown renderer with coarse-cluster flagging"
```

---

## Task 3: The diagnostics script (DB pull + wiring)

**Files:**
- Create: `app/scripts/phase_a_diagnostics.py`

This task wires the pure functions to the live DB. The DB-pull (`collect_observations`)
is integration code; the logic it feeds (`assemble_findings`/`render_markdown`) is already
unit-tested in Tasks 1–2, so this task adds the script and a `--help` smoke check only.

- [ ] **Step 1: Write the script**

Create `app/scripts/phase_a_diagnostics.py`:

```python
"""Phase A diagnostics — measure where clustering/extraction is too coarse.

Reads the *already-populated* Postgres (no writes, no schema changes), computes
per-cluster granularity metrics, and writes a Markdown + JSON findings artifact.

Usage::

    python -m app.scripts.phase_a_diagnostics
    python -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300
    python -m app.scripts.phase_a_diagnostics --no-dispersion   # skip vector load

If the DB has no clusters, the script prints guidance to seed first
(`python -m app.scripts.seed_data`) and exits 0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import func, select

from app.core.logging import configure_logging, get_logger
from app.db.models import (
    ClusterMemberORM,
    Embedding,
    ExtractedQuestionORM,
    SemanticCluster,
)
from app.db.session import async_session_maker
from app.diagnostics.cluster_metrics import mean_cosine_distance_to_centroid
from app.diagnostics.report import (
    ClusterObservation,
    assemble_findings,
    render_markdown,
)

logger = get_logger(__name__)

DEFAULT_OUT_DIR = Path("docs/superpowers/diagnostics")
FINDINGS_BASENAME = "2026-06-08-phase-a-findings"


async def _global_distributions(session) -> tuple[dict[str, int], dict[str, int]]:
    """QuestionType and Intent distributions across all extracted_questions."""
    qtype_rows = (
        await session.execute(
            select(ExtractedQuestionORM.question_type, func.count())
            .group_by(ExtractedQuestionORM.question_type)
        )
    ).all()
    intent_rows = (
        await session.execute(
            select(ExtractedQuestionORM.intent, func.count())
            .group_by(ExtractedQuestionORM.intent)
        )
    ).all()
    qtype = {str(k): int(v) for k, v in qtype_rows if k is not None}
    intent = {str(k): int(v) for k, v in intent_rows if k is not None}
    return qtype, intent


async def collect_observations(
    session,
    *,
    top_n: int,
    max_members: int,
    with_dispersion: bool,
) -> list[ClusterObservation]:
    """Pull the top-N clusters by frequency and build ClusterObservation rows."""
    cluster_rows = (
        await session.execute(
            select(SemanticCluster).order_by(SemanticCluster.frequency.desc()).limit(top_n)
        )
    ).scalars().all()

    observations: list[ClusterObservation] = []
    for cluster in cluster_rows:
        # Members joined to their question for intent + gloss, capped at max_members.
        member_rows = (
            await session.execute(
                select(
                    ExtractedQuestionORM.id,
                    ExtractedQuestionORM.intent,
                    ExtractedQuestionORM.english_gloss,
                    ExtractedQuestionORM.normalized_text,
                )
                .join(
                    ClusterMemberORM,
                    ClusterMemberORM.question_id == ExtractedQuestionORM.id,
                )
                .where(ClusterMemberORM.cluster_id == cluster.id)
                .limit(max_members)
            )
        ).all()

        member_intents = [str(r.intent) for r in member_rows if r.intent is not None]
        member_glosses = [
            (r.english_gloss or r.normalized_text or "")[:120] for r in member_rows
        ]

        dispersion: float | None = None
        if with_dispersion and cluster.centroid is not None and member_rows:
            q_ids = [r.id for r in member_rows]
            vec_rows = (
                await session.execute(
                    select(Embedding.vector).where(Embedding.question_id.in_(q_ids))
                )
            ).all()
            vectors = [list(v[0]) for v in vec_rows if v[0] is not None]
            if vectors:
                dispersion = mean_cosine_distance_to_centroid(
                    vectors, list(cluster.centroid)
                )

        observations.append(
            ClusterObservation(
                cluster_id=str(cluster.id),
                label=cluster.label,
                canonical_question=cluster.canonical_question,
                frequency=int(cluster.frequency or 0),
                size=len(member_rows),
                member_intents=member_intents,
                member_glosses=member_glosses,
                dispersion=dispersion,
            )
        )
    return observations


async def run(
    *,
    top_n: int,
    max_members: int,
    with_dispersion: bool,
    out_dir: Path,
    size_threshold: int,
    purity_threshold: float,
) -> dict:
    async with async_session_maker() as session:
        n_clusters = int(
            (await session.execute(select(func.count(SemanticCluster.id)))).scalar_one() or 0
        )
        if n_clusters == 0:
            logger.warning("phase_a.no_clusters")
            print(
                "No clusters found. Seed and run the pipeline first:\n"
                "  docker compose up -d --build\n"
                "  python -m app.scripts.seed_data --api-url http://localhost:8080\n"
            )
            return {}

        qtype_dist, intent_dist = await _global_distributions(session)
        observations = await collect_observations(
            session,
            top_n=top_n,
            max_members=max_members,
            with_dispersion=with_dispersion,
        )

    findings = assemble_findings(
        observations,
        qtype_distribution=qtype_dist,
        intent_distribution=intent_dist,
        size_threshold=size_threshold,
        purity_threshold=purity_threshold,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{FINDINGS_BASENAME}.md"
    json_path = out_dir / f"{FINDINGS_BASENAME}.json"
    md_path.write_text(render_markdown(findings), encoding="utf-8")
    json_path.write_text(json.dumps(findings, indent=2, default=str), encoding="utf-8")

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print(
        f"Clusters: {findings['n_clusters']} | Coarse: {findings['n_coarse']} "
        f"(size>={size_threshold}, purity<{purity_threshold})"
    )
    return findings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.scripts.phase_a_diagnostics",
        description="Phase A: measure clustering/extraction granularity (read-only).",
    )
    parser.add_argument("--top-n", type=int, default=30, help="Top clusters by frequency.")
    parser.add_argument("--max-members", type=int, default=300, help="Members sampled per cluster.")
    parser.add_argument(
        "--no-dispersion",
        action="store_true",
        help="Skip embedding load (faster; omits centroid dispersion).",
    )
    parser.add_argument("--size-threshold", type=int, default=10)
    parser.add_argument("--purity-threshold", type=float, default=0.6)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    asyncio.run(
        run(
            top_n=args.top_n,
            max_members=args.max_members,
            with_dispersion=not args.no_dispersion,
            out_dir=Path(args.out_dir),
            size_threshold=args.size_threshold,
            purity_threshold=args.purity_threshold,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke-check the CLI parses (no DB needed)**

Run: `python -m app.scripts.phase_a_diagnostics --help`
Expected: argparse help text prints, exit 0. Confirms imports resolve and the module loads.

- [ ] **Step 3: Run the full unit suite to confirm no regressions**

Run: `python -m pytest app/tests/unit/test_cluster_metrics.py app/tests/unit/test_diagnostics_report.py -v`
Expected: PASS (18 passed)

- [ ] **Step 4: Commit**

```bash
git add app/scripts/phase_a_diagnostics.py
git commit -m "feat(diagnostics): phase-A read-only DB-pull script for granularity findings"
```

---

## Task 4: Execute against the live DB and capture findings

This task runs the tool against your already-populated database (the one that
feels "too coarse") and produces the findings artifact that drives Phases B/C.

- [ ] **Step 1: Ensure the stack is up and the DB has data**

Run:
```bash
docker compose up -d --build
python -m app.scripts.seed_data --api-url http://localhost:8080   # only if DB is empty
```
Expected: containers healthy; if seeding, a summary with `uploaded > 0`. (If you already have a populated DB, skip the seed.)

> Note: `seed_data` ingests `data/sample_transcripts/*.json` (6 calls) — enough to smoke-test the tool, but coarseness conclusions should be drawn from your real production-volume DB. Point the stack at that DB via `.env` if the sample set is too small to be representative.

- [ ] **Step 2: Run the diagnostics**

Run: `python -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300`
Expected: prints `Wrote docs/superpowers/diagnostics/2026-06-08-phase-a-findings.md`, the JSON sidecar, and a one-line `Clusters: N | Coarse: M` summary.

- [ ] **Step 3: Read the findings and record the Phase B/C decisions**

Open `docs/superpowers/diagnostics/2026-06-08-phase-a-findings.md` and answer, in a short
"Decisions" section appended to that file:
  1. **Is clustering too coarse?** Look at `n_coarse` and the coarse-cluster table — are distinct issues (different intents / sample glosses) sharing one cluster?
  2. **Tuning direction:** if coarse, Phase B should lower `hdbscan_min_cluster_size` / adjust `hdbscan_min_samples` (`app/core/config.py`); record the current values and a target to try.
  3. **Discrepancy separability:** does `QuestionType` distribution show meaningful `complaint` volume, or is everything `question`? This decides how much Phase C's `discrepancy_type` field must carry.
  4. **Claim/affidavit visibility:** are `claim_process` / `claim_rejection` / `document_request` distinguishable, or lumped? This decides the Phase B sub-taxonomy.

- [ ] **Step 4: Commit the findings artifact**

```bash
git add docs/superpowers/diagnostics/2026-06-08-phase-a-findings.md docs/superpowers/diagnostics/2026-06-08-phase-a-findings.json
git commit -m "docs(diagnostics): phase-A findings on clustering/extraction granularity"
```

- [ ] **Step 5: Hand back for Phase B/C planning**

Phase A is complete. Report the findings summary (n_coarse, coarse-cluster examples,
qtype distribution, claim-intent visibility) and request a new planning pass for
**Phase B** (targeted extension + clustering tuning) and **Phase C** (discrepancy
dimension), now grounded in real numbers rather than assumptions.

---

## Self-Review

**Spec coverage:**
- Spec §3 (Phase A — verify & quantify coarseness): Tasks 1–4 implement intent distribution, top clusters, cluster size distribution, intra-cluster heterogeneity (purity/entropy/dispersion), and question-vs-discrepancy separability (QuestionType distribution). ✔
- Spec §3 acceptance ("quantitatively pin down where granularity fails" → defines B/C): Task 4 Step 3 records the explicit B/C decisions in the findings doc. ✔
- Spec §4/§5 (Phases B/C): intentionally **not** in this plan — deferred pending Task 4 findings, per the spec's "Phase A is a gate." ✔

**Placeholder scan:** No TBD/TODO/"handle edge cases"/uncoded steps — every code step shows full content. ✔

**Type consistency:** `ClusterObservation` fields (`cluster_id`, `label`, `canonical_question`, `frequency`, `size`, `member_intents`, `member_glosses`, `dispersion`) are defined in Task 2 and used identically in Task 3's `collect_observations`. `assemble_findings` / `render_markdown` / `flag_coarse` signatures match between Task 2's definition, the Task 2 tests, and the Task 3 caller. Metric function names (`normalized_entropy`, `intent_purity`, `size_stats`, `mean_cosine_distance_to_centroid`) match between Task 1 and their callers in `report.py` / the script. ✔
