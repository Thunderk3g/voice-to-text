"""
SQLAlchemy 2.0 typed ORM models for the v2t platform.

Tables (9 total) — must match column names in `docs/contracts.md`:
    calls, utterances, extracted_questions, embeddings,
    semantic_clusters, cluster_members, canonical_faqs,
    memory_edges, feedback_annotations.

Notes:
- UUID PKs use `gen_random_uuid()` on the server side (pgcrypto in pg16).
- Vector columns are `pgvector.sqlalchemy.Vector(1024)`.
- PG enums are built from `app.models.enums` via `values_callable` so the
  *string values* (e.g. "AGENT", "policy_details") are stored, matching the
  StrEnum definitions and the Pydantic schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    CallStatus,
    EdgeRelation,
    FeedbackAction,
    Intent,
    Language,
    QuestionType,
    Speaker,
)

# ---------------------------------------------------------------------------
# PG enum types (named, reusable across tables)
# ---------------------------------------------------------------------------
SpeakerEnum = SAEnum(
    Speaker,
    name="speaker",
    values_callable=lambda e: [m.value for m in e],
)
LanguageEnum = SAEnum(
    Language,
    name="language",
    values_callable=lambda e: [m.value for m in e],
)
IntentEnum = SAEnum(
    Intent,
    name="intent",
    values_callable=lambda e: [m.value for m in e],
)
CallStatusEnum = SAEnum(
    CallStatus,
    name="call_status",
    values_callable=lambda e: [m.value for m in e],
)
QuestionTypeEnum = SAEnum(
    QuestionType,
    name="question_type",
    values_callable=lambda e: [m.value for m in e],
)
FeedbackActionEnum = SAEnum(
    FeedbackAction,
    name="feedback_action",
    values_callable=lambda e: [m.value for m in e],
)
EdgeRelationEnum = SAEnum(
    EdgeRelation,
    name="edge_relation",
    values_callable=lambda e: [m.value for m in e],
)

EMBEDDING_DIM = 1024


# ---------------------------------------------------------------------------
# 1. calls
# ---------------------------------------------------------------------------
class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    is_transcript: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[CallStatus] = mapped_column(
        CallStatusEnum, nullable=False, default=CallStatus.PENDING
    )
    detected_language: Mapped[Language | None] = mapped_column(
        LanguageEnum, nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    langsmith_trace_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    utterances: Mapped[list["Utterance"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )
    questions: Mapped[list["ExtractedQuestionORM"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 2. utterances
# ---------------------------------------------------------------------------
class Utterance(Base):
    __tablename__ = "utterances"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    speaker: Mapped[Speaker] = mapped_column(
        SpeakerEnum, nullable=False, default=Speaker.UNKNOWN
    )
    start_ts: Mapped[float] = mapped_column(Float, nullable=False)
    end_ts: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[Language] = mapped_column(LanguageEnum, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    words: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    call: Mapped[Call] = relationship(back_populates="utterances")


# ---------------------------------------------------------------------------
# 3. extracted_questions
# ---------------------------------------------------------------------------
class ExtractedQuestionORM(Base):
    __tablename__ = "extracted_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    utterance_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("utterances.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    english_gloss: Mapped[str | None] = mapped_column(Text, nullable=True)

    question_type: Mapped[QuestionType] = mapped_column(
        QuestionTypeEnum, nullable=False, default=QuestionType.QUESTION
    )
    intent: Mapped[Intent] = mapped_column(
        IntentEnum, nullable=False, default=Intent.OTHER, index=True
    )
    secondary_intents: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )

    language: Mapped[Language] = mapped_column(LanguageEnum, nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set true by feedback tasks (merge / split / relabel) so downstream
    # learning treats these rows as gold labels.
    human_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    call: Mapped[Call] = relationship(back_populates="questions")
    embedding: Mapped["Embedding | None"] = relationship(
        back_populates="question", uselist=False, cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 4. embeddings
# ---------------------------------------------------------------------------
class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extracted_questions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False, default=EMBEDDING_DIM)
    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    question: Mapped[ExtractedQuestionORM] = relationship(back_populates="embedding")


# ---------------------------------------------------------------------------
# 5. semantic_clusters
# ---------------------------------------------------------------------------
class SemanticCluster(Base):
    __tablename__ = "semantic_clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    centroid: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    dominant_language: Mapped[Language] = mapped_column(LanguageEnum, nullable=False)
    dominant_intents: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    is_stable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    members: Mapped[list["ClusterMemberORM"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )
    canonical_faqs: Mapped[list["CanonicalFAQORM"]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 6. cluster_members
# ---------------------------------------------------------------------------
class ClusterMemberORM(Base):
    __tablename__ = "cluster_members"

    # `question_id` is PK per contracts.md; one question -> one cluster at a time.
    question_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extracted_questions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    similarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    cluster: Mapped[SemanticCluster] = relationship(back_populates="members")


# ---------------------------------------------------------------------------
# 7. canonical_faqs
# ---------------------------------------------------------------------------
class CanonicalFAQORM(Base):
    __tablename__ = "canonical_faqs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_question: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_question_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[Language] = mapped_column(LanguageEnum, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    cluster: Mapped[SemanticCluster] = relationship(back_populates="canonical_faqs")


# ---------------------------------------------------------------------------
# 8. memory_edges
# ---------------------------------------------------------------------------
class MemoryEdgeORM(Base):
    __tablename__ = "memory_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_cluster_id",
            "target_cluster_id",
            "relation",
            name="uq_memory_edges_source_target_relation",
        ),
        Index("ix_memory_edges_source_cluster_id", "source_cluster_id"),
        Index("ix_memory_edges_target_cluster_id", "target_cluster_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    source_cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_cluster_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[EdgeRelation] = mapped_column(EdgeRelationEnum, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# 9. feedback_annotations
# ---------------------------------------------------------------------------
class FeedbackAnnotationORM(Base):
    __tablename__ = "feedback_annotations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    action: Mapped[FeedbackAction] = mapped_column(FeedbackActionEnum, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "Base",
    "Call",
    "Utterance",
    "ExtractedQuestionORM",
    "Embedding",
    "SemanticCluster",
    "ClusterMemberORM",
    "CanonicalFAQORM",
    "MemoryEdgeORM",
    "FeedbackAnnotationORM",
    "EMBEDDING_DIM",
]
