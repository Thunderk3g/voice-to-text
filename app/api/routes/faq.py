"""
GET /faq — canonical FAQ list, ordered by cluster frequency.

Used by the dashboard's FAQ tab and by downstream retrieval-augmented
generation flows that prefer canonical phrasings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.models.enums import Intent, Language
from app.models.schemas import CanonicalFAQ

router = APIRouter(tags=["faq"])


@router.get("/faq", response_model=list[CanonicalFAQ])
async def list_faqs(
    intent: Intent | None = Query(default=None),
    language: Language | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[CanonicalFAQ]:
    from app.db.models import CanonicalFAQ as FAQORM, SemanticCluster

    stmt = (
        select(FAQORM)
        .join(SemanticCluster, SemanticCluster.id == FAQORM.cluster_id)
        .order_by(SemanticCluster.frequency.desc())
    )
    if language is not None:
        stmt = stmt.where(FAQORM.language == language)
    if intent is not None:
        # SemanticCluster.dominant_intents is an ARRAY/JSON of intents.
        stmt = stmt.where(SemanticCluster.dominant_intents.any(intent))  # type: ignore[attr-defined]

    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [CanonicalFAQ.model_validate(r) for r in rows]
