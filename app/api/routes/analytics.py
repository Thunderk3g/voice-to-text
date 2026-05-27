"""
GET /analytics — high-level KPIs for the dashboard landing page.

Computes:
  - counts of calls, questions, clusters
  - language + intent distributions across `extracted_questions`
  - top clusters by frequency
  - 14-day rolling cluster growth (new vs churned)
  - emerging-topic drift signals (recent clusters with rising freq)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.models.enums import Intent, Language
from app.models.schemas import AnalyticsSummary

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsSummary)
async def get_analytics(db: AsyncSession = Depends(get_db)) -> AnalyticsSummary:
    from app.db.models import (
        Call,
        ExtractedQuestion as ExtractedQuestionORM,
        SemanticCluster,
    )

    total_calls = int((await db.execute(select(func.count(Call.id)))).scalar_one() or 0)
    total_questions = int(
        (await db.execute(select(func.count(ExtractedQuestionORM.id)))).scalar_one() or 0
    )
    total_clusters = int(
        (await db.execute(select(func.count(SemanticCluster.id)))).scalar_one() or 0
    )

    # Language distribution
    lang_rows = (
        await db.execute(
            select(ExtractedQuestionORM.language, func.count())
            .group_by(ExtractedQuestionORM.language)
        )
    ).all()
    language_distribution: dict[Language, int] = {
        Language(lang): int(count) for lang, count in lang_rows if lang is not None
    }

    # Intent distribution
    intent_rows = (
        await db.execute(
            select(ExtractedQuestionORM.intent, func.count())
            .group_by(ExtractedQuestionORM.intent)
        )
    ).all()
    intent_distribution: dict[Intent, int] = {
        Intent(i): int(count) for i, count in intent_rows if i is not None
    }

    # Top clusters by frequency
    top_cluster_rows = (
        await db.execute(
            select(
                SemanticCluster.id,
                SemanticCluster.label,
                SemanticCluster.canonical_question,
                SemanticCluster.frequency,
                SemanticCluster.dominant_language,
            )
            .order_by(SemanticCluster.frequency.desc())
            .limit(20)
        )
    ).all()
    top_clusters: list[dict[str, Any]] = [
        {
            "cluster_id": str(row.id),
            "label": row.label,
            "canonical_question": row.canonical_question,
            "frequency": int(row.frequency or 0),
            "dominant_language": row.dominant_language,
        }
        for row in top_cluster_rows
    ]

    # 14-day cluster growth
    today = datetime.now(timezone.utc).date()
    growth_buckets: list[dict[str, Any]] = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        new_clusters = int(
            (
                await db.execute(
                    select(func.count(SemanticCluster.id))
                    .where(SemanticCluster.last_updated >= day_start)
                    .where(SemanticCluster.last_updated < day_end)
                )
            ).scalar_one()
            or 0
        )
        churned_clusters = int(
            (
                await db.execute(
                    select(func.count(SemanticCluster.id))
                    .where(SemanticCluster.last_updated >= day_start)
                    .where(SemanticCluster.last_updated < day_end)
                    .where(SemanticCluster.is_stable.is_(False))
                )
            ).scalar_one()
            or 0
        )
        growth_buckets.append(
            {
                "date": day.isoformat(),
                "new_clusters": new_clusters,
                "churned_clusters": churned_clusters,
            }
        )

    # Emerging topics: clusters touched in the last 3 days, sorted by frequency.
    drift_threshold = datetime.now(timezone.utc) - timedelta(days=3)
    emerging_rows = (
        await db.execute(
            select(
                SemanticCluster.id,
                SemanticCluster.label,
                SemanticCluster.canonical_question,
                SemanticCluster.frequency,
                SemanticCluster.last_updated,
            )
            .where(SemanticCluster.last_updated >= drift_threshold)
            .order_by(SemanticCluster.frequency.desc())
            .limit(10)
        )
    ).all()
    emerging_topics: list[dict[str, Any]] = [
        {
            "cluster_id": str(row.id),
            "label": row.label,
            "canonical_question": row.canonical_question,
            "frequency": int(row.frequency or 0),
            "last_updated": row.last_updated.isoformat() if row.last_updated else None,
        }
        for row in emerging_rows
    ]

    return AnalyticsSummary(
        total_calls=total_calls,
        total_questions=total_questions,
        total_clusters=total_clusters,
        language_distribution=language_distribution,
        intent_distribution=intent_distribution,
        top_clusters=top_clusters,
        cluster_growth=growth_buckets,
        emerging_topics=emerging_topics,
    )
