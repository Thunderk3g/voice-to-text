"""
Async repository classes — one per aggregate root.

Every repo:
- Takes an `AsyncSession` in its constructor.
- Exposes typed `create / get / list / update_status / bulk_insert` where
  meaningful, plus aggregate-specific helpers (e.g. neighbor search via
  pgvector cosine distance).
- Returns Pydantic schemas from `app.models.schemas`, never raw ORM objects,
  via `Schema.model_validate(orm_obj, from_attributes=True)`.

The DB session is owned by the caller (FastAPI dep or worker context); repos
do not commit unless a method's docstring says otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CanonicalFAQORM,
    Call,
    ClusterMemberORM,
    Embedding,
    ExtractedQuestionORM,
    FeedbackAnnotationORM,
    MemoryEdgeORM,
    SemanticCluster,
    Utterance,
)
from app.models.enums import CallStatus, Intent, Language
from app.models.schemas import (
    CallCreate,
    CallMetadata,
    CallRead,
    CanonicalFAQ,
    ClusterMember,
    ClusterRecord,
    EmbeddingRecord,
    ExtractedQuestion,
    FeedbackAnnotation,
    MemoryEdge,
    UtteranceSchema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _call_to_schema(orm: Call) -> CallRead:
    """ORM Call -> CallRead. metadata column name differs from attribute."""
    return CallRead(
        id=orm.id,
        source_uri=orm.source_uri,
        is_transcript=orm.is_transcript,
        status=orm.status,
        detected_language=orm.detected_language,
        duration_seconds=orm.duration_seconds,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        metadata=CallMetadata(**(orm.call_metadata or {})),
    )


def _question_to_schema(orm: ExtractedQuestionORM) -> ExtractedQuestion:
    return ExtractedQuestion(
        id=orm.id,
        call_id=orm.call_id,
        utterance_id=orm.utterance_id,
        raw_text=orm.raw_text,
        normalized_text=orm.normalized_text,
        english_gloss=orm.english_gloss,
        question_type=orm.question_type,
        intent=orm.intent,
        secondary_intents=[Intent(v) for v in (orm.secondary_intents or [])],
        language=orm.language,
        confidence=orm.confidence,
        extracted_at=orm.extracted_at,
    )


def _cluster_to_schema(orm: SemanticCluster) -> ClusterRecord:
    return ClusterRecord(
        id=orm.id,
        label=orm.label,
        canonical_question=orm.canonical_question,
        centroid=list(orm.centroid) if orm.centroid is not None else [],
        dominant_language=orm.dominant_language,
        dominant_intents=[Intent(v) for v in (orm.dominant_intents or [])],
        frequency=orm.frequency,
        last_updated=orm.last_updated,
        is_stable=orm.is_stable,
    )


# ---------------------------------------------------------------------------
# CallRepo
# ---------------------------------------------------------------------------
class CallRepo:
    """Aggregate root for calls."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: CallCreate) -> CallRead:
        orm = Call(
            source_uri=payload.source_uri,
            is_transcript=payload.is_transcript,
            status=CallStatus.PENDING,
            call_metadata=payload.metadata.model_dump(mode="json"),
        )
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return _call_to_schema(orm)

    async def get(self, call_id: UUID) -> CallRead | None:
        orm = await self.session.get(Call, call_id)
        return _call_to_schema(orm) if orm else None

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[CallRead]:
        stmt = select(Call).order_by(Call.created_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_call_to_schema(r) for r in rows]

    async def update_status(
        self,
        call_id: UUID,
        status: CallStatus,
        *,
        detected_language: Language | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": datetime.utcnow()}
        if detected_language is not None:
            values["detected_language"] = detected_language
        if duration_seconds is not None:
            values["duration_seconds"] = duration_seconds
        await self.session.execute(update(Call).where(Call.id == call_id).values(**values))


# ---------------------------------------------------------------------------
# UtteranceRepo
# ---------------------------------------------------------------------------
class UtteranceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_insert(self, items: Sequence[UtteranceSchema]) -> list[UtteranceSchema]:
        orms = [
            Utterance(
                call_id=u.call_id,
                speaker=u.speaker,
                start_ts=u.start_ts,
                end_ts=u.end_ts,
                text=u.text,
                language=u.language,
                confidence=u.confidence,
                words=u.words,
            )
            for u in items
        ]
        self.session.add_all(orms)
        await self.session.flush()
        for orm in orms:
            await self.session.refresh(orm)
        return [UtteranceSchema.model_validate(o, from_attributes=True) for o in orms]

    async def list(self, call_id: UUID) -> list[UtteranceSchema]:
        stmt = (
            select(Utterance)
            .where(Utterance.call_id == call_id)
            .order_by(Utterance.start_ts.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [UtteranceSchema.model_validate(r, from_attributes=True) for r in rows]

    async def get(self, utterance_id: UUID) -> UtteranceSchema | None:
        orm = await self.session.get(Utterance, utterance_id)
        return UtteranceSchema.model_validate(orm, from_attributes=True) if orm else None


# ---------------------------------------------------------------------------
# QuestionRepo
# ---------------------------------------------------------------------------
class QuestionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_insert(self, items: Sequence[ExtractedQuestion]) -> list[ExtractedQuestion]:
        orms = [
            ExtractedQuestionORM(
                call_id=q.call_id,
                utterance_id=q.utterance_id,
                raw_text=q.raw_text,
                normalized_text=q.normalized_text,
                english_gloss=q.english_gloss,
                question_type=q.question_type,
                intent=q.intent,
                secondary_intents=[i.value for i in q.secondary_intents],
                language=q.language,
                confidence=q.confidence,
            )
            for q in items
        ]
        self.session.add_all(orms)
        await self.session.flush()
        for orm in orms:
            await self.session.refresh(orm)
        return [_question_to_schema(o) for o in orms]

    async def create(self, q: ExtractedQuestion) -> ExtractedQuestion:
        result = await self.bulk_insert([q])
        return result[0]

    async def get(self, question_id: UUID) -> ExtractedQuestion | None:
        orm = await self.session.get(ExtractedQuestionORM, question_id)
        return _question_to_schema(orm) if orm else None

    async def list(
        self,
        *,
        call_id: UUID | None = None,
        intent: Intent | None = None,
        language: Language | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractedQuestion]:
        stmt = select(ExtractedQuestionORM)
        if call_id is not None:
            stmt = stmt.where(ExtractedQuestionORM.call_id == call_id)
        if intent is not None:
            stmt = stmt.where(ExtractedQuestionORM.intent == intent)
        if language is not None:
            stmt = stmt.where(ExtractedQuestionORM.language == language)
        stmt = stmt.order_by(ExtractedQuestionORM.extracted_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_question_to_schema(r) for r in rows]


# ---------------------------------------------------------------------------
# EmbeddingRepo
# ---------------------------------------------------------------------------
class EmbeddingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_insert(self, items: Sequence[EmbeddingRecord]) -> list[EmbeddingRecord]:
        orms = [
            Embedding(
                question_id=e.question_id,
                model=e.model,
                dim=e.dim,
                vector=e.vector,
            )
            for e in items
        ]
        self.session.add_all(orms)
        await self.session.flush()
        for orm in orms:
            await self.session.refresh(orm)
        return [EmbeddingRecord.model_validate(o, from_attributes=True) for o in orms]

    async def create(self, record: EmbeddingRecord) -> EmbeddingRecord:
        out = await self.bulk_insert([record])
        return out[0]

    async def get(self, embedding_id: UUID) -> EmbeddingRecord | None:
        orm = await self.session.get(Embedding, embedding_id)
        return EmbeddingRecord.model_validate(orm, from_attributes=True) if orm else None

    async def get_by_question_id(self, question_id: UUID) -> EmbeddingRecord | None:
        stmt = select(Embedding).where(Embedding.question_id == question_id)
        orm = (await self.session.execute(stmt)).scalar_one_or_none()
        return EmbeddingRecord.model_validate(orm, from_attributes=True) if orm else None

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[EmbeddingRecord]:
        stmt = (
            select(Embedding)
            .order_by(Embedding.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [EmbeddingRecord.model_validate(r, from_attributes=True) for r in rows]

    async def get_neighbors_by_embedding(
        self,
        vector: list[float],
        *,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[EmbeddingRecord, float]]:
        """Cosine-nearest neighbors using pgvector's `<=>` operator.

        Returns a list of (EmbeddingRecord, cosine_similarity) pairs ordered
        by descending similarity. `min_score` is applied as a similarity cutoff.
        """
        # cosine_distance returns distance in [0, 2]; similarity = 1 - distance.
        distance = Embedding.vector.cosine_distance(vector).label("distance")
        stmt = (
            select(Embedding, distance)
            .order_by(distance.asc())
            .limit(top_k)
        )
        rows = (await self.session.execute(stmt)).all()
        out: list[tuple[EmbeddingRecord, float]] = []
        for orm, dist in rows:
            sim = 1.0 - float(dist)
            if sim < min_score:
                continue
            out.append((EmbeddingRecord.model_validate(orm, from_attributes=True), sim))
        return out


# ---------------------------------------------------------------------------
# ClusterRepo
# ---------------------------------------------------------------------------
class ClusterRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        centroid: list[float],
        dominant_language: Language,
        dominant_intents: Iterable[Intent],
        label: str | None = None,
        canonical_question: str | None = None,
        frequency: int = 0,
        is_stable: bool = True,
    ) -> ClusterRecord:
        orm = SemanticCluster(
            label=label,
            canonical_question=canonical_question,
            centroid=centroid,
            dominant_language=dominant_language,
            dominant_intents=[i.value for i in dominant_intents],
            frequency=frequency,
            is_stable=is_stable,
        )
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return _cluster_to_schema(orm)

    async def get(self, cluster_id: UUID) -> ClusterRecord | None:
        orm = await self.session.get(SemanticCluster, cluster_id)
        return _cluster_to_schema(orm) if orm else None

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ClusterRecord]:
        stmt = (
            select(SemanticCluster)
            .order_by(SemanticCluster.frequency.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_cluster_to_schema(r) for r in rows]

    async def update_status(
        self,
        cluster_id: UUID,
        *,
        is_stable: bool | None = None,
        frequency: int | None = None,
        label: str | None = None,
        canonical_question: str | None = None,
        centroid: list[float] | None = None,
    ) -> None:
        values: dict[str, Any] = {"last_updated": datetime.utcnow()}
        if is_stable is not None:
            values["is_stable"] = is_stable
        if frequency is not None:
            values["frequency"] = frequency
        if label is not None:
            values["label"] = label
        if canonical_question is not None:
            values["canonical_question"] = canonical_question
        if centroid is not None:
            values["centroid"] = centroid
        await self.session.execute(
            update(SemanticCluster).where(SemanticCluster.id == cluster_id).values(**values)
        )

    async def get_neighbors_by_embedding(
        self,
        vector: list[float],
        *,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> list[tuple[ClusterRecord, float]]:
        """Cosine-nearest clusters by centroid."""
        distance = SemanticCluster.centroid.cosine_distance(vector).label("distance")
        stmt = (
            select(SemanticCluster, distance)
            .order_by(distance.asc())
            .limit(top_k)
        )
        rows = (await self.session.execute(stmt)).all()
        out: list[tuple[ClusterRecord, float]] = []
        for orm, dist in rows:
            sim = 1.0 - float(dist)
            if sim < min_score:
                continue
            out.append((_cluster_to_schema(orm), sim))
        return out


# ---------------------------------------------------------------------------
# ClusterMemberRepo
# ---------------------------------------------------------------------------
class ClusterMemberRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_insert(self, items: Sequence[ClusterMember]) -> list[ClusterMember]:
        """Idempotent upsert on `question_id` (PK)."""
        if not items:
            return []
        rows = [
            {
                "cluster_id": m.cluster_id,
                "question_id": m.question_id,
                "similarity": m.similarity,
                "assigned_at": m.assigned_at,
            }
            for m in items
        ]
        stmt = pg_insert(ClusterMemberORM).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ClusterMemberORM.question_id],
            set_={
                "cluster_id": stmt.excluded.cluster_id,
                "similarity": stmt.excluded.similarity,
                "assigned_at": stmt.excluded.assigned_at,
            },
        )
        await self.session.execute(stmt)
        return list(items)

    async def create(self, member: ClusterMember) -> ClusterMember:
        out = await self.bulk_insert([member])
        return out[0]

    async def get(self, question_id: UUID) -> ClusterMember | None:
        orm = await self.session.get(ClusterMemberORM, question_id)
        return ClusterMember.model_validate(orm, from_attributes=True) if orm else None

    async def list(
        self,
        *,
        cluster_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ClusterMember]:
        stmt = select(ClusterMemberORM)
        if cluster_id is not None:
            stmt = stmt.where(ClusterMemberORM.cluster_id == cluster_id)
        stmt = stmt.order_by(ClusterMemberORM.assigned_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [ClusterMember.model_validate(r, from_attributes=True) for r in rows]

    async def delete_for_cluster(self, cluster_id: UUID) -> None:
        await self.session.execute(
            delete(ClusterMemberORM).where(ClusterMemberORM.cluster_id == cluster_id)
        )


# ---------------------------------------------------------------------------
# FAQRepo
# ---------------------------------------------------------------------------
class FAQRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, faq: CanonicalFAQ) -> CanonicalFAQ:
        orm = CanonicalFAQORM(
            cluster_id=faq.cluster_id,
            canonical_question=faq.canonical_question,
            canonical_question_en=faq.canonical_question_en,
            suggested_answer=faq.suggested_answer,
            language=faq.language,
            confidence=faq.confidence,
            version=faq.version,
        )
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return CanonicalFAQ.model_validate(orm, from_attributes=True)

    async def bulk_insert(self, items: Sequence[CanonicalFAQ]) -> list[CanonicalFAQ]:
        out: list[CanonicalFAQ] = []
        for f in items:
            out.append(await self.create(f))
        return out

    async def get(self, faq_id: UUID) -> CanonicalFAQ | None:
        orm = await self.session.get(CanonicalFAQORM, faq_id)
        return CanonicalFAQ.model_validate(orm, from_attributes=True) if orm else None

    async def get_latest_for_cluster(self, cluster_id: UUID) -> CanonicalFAQ | None:
        stmt = (
            select(CanonicalFAQORM)
            .where(CanonicalFAQORM.cluster_id == cluster_id)
            .order_by(CanonicalFAQORM.version.desc(), CanonicalFAQORM.created_at.desc())
            .limit(1)
        )
        orm = (await self.session.execute(stmt)).scalar_one_or_none()
        return CanonicalFAQ.model_validate(orm, from_attributes=True) if orm else None

    async def list(
        self,
        *,
        language: Language | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CanonicalFAQ]:
        stmt = select(CanonicalFAQORM)
        if language is not None:
            stmt = stmt.where(CanonicalFAQORM.language == language)
        stmt = stmt.order_by(CanonicalFAQORM.updated_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [CanonicalFAQ.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# MemoryEdgeRepo
# ---------------------------------------------------------------------------
class MemoryEdgeRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_insert(self, items: Sequence[MemoryEdge]) -> list[MemoryEdge]:
        """Upsert on (source, target, relation) — increments weight on conflict."""
        if not items:
            return []
        rows = [
            {
                "source_cluster_id": e.source_cluster_id,
                "target_cluster_id": e.target_cluster_id,
                "relation": e.relation,
                "weight": e.weight,
                "reason": e.reason,
            }
            for e in items
        ]
        stmt = pg_insert(MemoryEdgeORM).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_memory_edges_source_target_relation",
            set_={
                "weight": stmt.excluded.weight,
                "reason": stmt.excluded.reason,
            },
        )
        await self.session.execute(stmt)
        return list(items)

    async def create(self, edge: MemoryEdge) -> MemoryEdge:
        out = await self.bulk_insert([edge])
        return out[0]

    async def get(self, edge_id: UUID) -> MemoryEdge | None:
        orm = await self.session.get(MemoryEdgeORM, edge_id)
        return MemoryEdge.model_validate(orm, from_attributes=True) if orm else None

    async def list(
        self,
        *,
        min_weight: float = 0.0,
        limit: int = 500,
        offset: int = 0,
    ) -> list[MemoryEdge]:
        stmt = (
            select(MemoryEdgeORM)
            .where(MemoryEdgeORM.weight >= min_weight)
            .order_by(MemoryEdgeORM.weight.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [MemoryEdge.model_validate(r, from_attributes=True) for r in rows]

    async def delete_for_cluster(self, cluster_id: UUID) -> None:
        await self.session.execute(
            delete(MemoryEdgeORM).where(
                (MemoryEdgeORM.source_cluster_id == cluster_id)
                | (MemoryEdgeORM.target_cluster_id == cluster_id)
            )
        )


# ---------------------------------------------------------------------------
# FeedbackRepo
# ---------------------------------------------------------------------------
class FeedbackRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, annotation: FeedbackAnnotation) -> FeedbackAnnotation:
        orm = FeedbackAnnotationORM(
            action=annotation.action,
            payload=annotation.payload,
            author=annotation.author,
            note=annotation.note,
        )
        self.session.add(orm)
        await self.session.flush()
        await self.session.refresh(orm)
        return FeedbackAnnotation.model_validate(orm, from_attributes=True)

    async def bulk_insert(
        self, items: Sequence[FeedbackAnnotation]
    ) -> list[FeedbackAnnotation]:
        out: list[FeedbackAnnotation] = []
        for a in items:
            out.append(await self.create(a))
        return out

    async def get(self, feedback_id: UUID) -> FeedbackAnnotation | None:
        orm = await self.session.get(FeedbackAnnotationORM, feedback_id)
        return FeedbackAnnotation.model_validate(orm, from_attributes=True) if orm else None

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[FeedbackAnnotation]:
        stmt = (
            select(FeedbackAnnotationORM)
            .order_by(FeedbackAnnotationORM.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [FeedbackAnnotation.model_validate(r, from_attributes=True) for r in rows]


__all__ = [
    "CallRepo",
    "UtteranceRepo",
    "QuestionRepo",
    "EmbeddingRepo",
    "ClusterRepo",
    "ClusterMemberRepo",
    "FAQRepo",
    "MemoryEdgeRepo",
    "FeedbackRepo",
]
