"""
POST /search — multilingual semantic retrieval.

Pipeline:
  1. Embed the query with role="query" (e5 prefix-aware).
  2. pgvector cosine search across `embeddings`, joined to
     `extracted_questions` for intent/language filters.
  3. Aggregate top hits by cluster to surface dominant intents.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_embedding_service
from app.api.errors import APIError
from app.core.logging import get_logger
from app.models.schemas import (
    ExtractedQuestion,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

logger = get_logger(__name__)
router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    if not payload.query.strip():
        raise APIError(
            "Empty query.",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_type="empty_query",
        )

    embedding_service = get_embedding_service()
    try:
        vectors = await embedding_service.embed([payload.query], role="query")
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_embed_failed", error=str(exc))
        raise APIError(
            "Failed to embed query.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_type="embed_failed",
        ) from exc

    if not vectors:
        return SearchResponse(query=payload.query, hits=[], cluster_aggregates=[])
    qvec = vectors[0]

    from app.db.models import (
        ClusterMemberORM,
        Embedding,
        ExtractedQuestionORM,
    )

    # pgvector cosine distance operator: `<=>`. Similarity = 1 - distance.
    distance_expr = Embedding.vector.cosine_distance(qvec)
    similarity_expr = (1.0 - distance_expr).label("similarity")

    stmt = (
        select(ExtractedQuestionORM, ClusterMemberORM.cluster_id, similarity_expr)
        .join(Embedding, Embedding.question_id == ExtractedQuestionORM.id)
        .outerjoin(ClusterMemberORM, ClusterMemberORM.question_id == ExtractedQuestionORM.id)
    )

    if payload.language is not None:
        stmt = stmt.where(ExtractedQuestionORM.language == payload.language)
    if payload.intents:
        stmt = stmt.where(ExtractedQuestionORM.intent.in_(payload.intents))

    # Order by the *similarity label* (descending), NOT by re-emitting
    # ``distance_expr`` in the ORDER BY. Reusing the same pgvector
    # ``<=>`` expression object in both the SELECT and the ORDER BY makes
    # asyncpg mis-bind the vector parameter and the query silently returns
    # ZERO rows (the join itself yields rows fine). Ordering by the selected
    # alias references the column once and is semantically identical
    # (ascending distance == descending similarity).
    stmt = stmt.order_by(similarity_expr.desc()).limit(payload.top_k)

    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_query_failed", error=str(exc))
        raise APIError(
            "Search query failed.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="search_query_failed",
        ) from exc

    hits: list[SearchHit] = []
    cluster_buckets: dict[Any, list[float]] = defaultdict(list)
    cluster_intents: dict[Any, Counter[str]] = defaultdict(Counter)

    for question_row, cluster_id, similarity in rows:
        score = float(similarity) if similarity is not None else 0.0
        if score < payload.min_score:
            continue
        question_schema = ExtractedQuestion.model_validate(question_row)
        hits.append(SearchHit(question=question_schema, cluster_id=cluster_id, score=score))
        if cluster_id is not None:
            cluster_buckets[cluster_id].append(score)
            cluster_intents[cluster_id][str(question_schema.intent)] += 1

    aggregates: list[dict[str, Any]] = []
    for cluster_id, scores in cluster_buckets.items():
        top_intent, _ = cluster_intents[cluster_id].most_common(1)[0]
        aggregates.append(
            {
                "cluster_id": str(cluster_id),
                "hit_count": len(scores),
                "max_score": max(scores),
                "avg_score": sum(scores) / len(scores),
                "top_intent": top_intent,
            }
        )
    aggregates.sort(key=lambda item: item["max_score"], reverse=True)

    return SearchResponse(query=payload.query, hits=hits, cluster_aggregates=aggregates)
