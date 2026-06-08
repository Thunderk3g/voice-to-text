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
