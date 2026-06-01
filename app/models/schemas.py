"""
Pydantic schemas — the contract between API, workers, services, and DB.

These are *transport* models. SQLAlchemy ORM models live in app/db/models.py
and convert to/from these via `.from_orm()` / `model_validate()`.

DO NOT add business logic here — keep schemas declarative.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    CallStatus,
    EdgeRelation,
    FeedbackAction,
    Intent,
    Language,
    QuestionType,
    Speaker,
)


# ============================================================================
# Calls
# ============================================================================
class CallMetadata(BaseModel):
    """Optional metadata supplied at ingest time."""

    agent_id: str | None = None
    customer_id: str | None = None
    campaign: str | None = None
    channel: str | None = None  # ivr | inbound | outbound | chat-bot
    received_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CallCreate(BaseModel):
    """Payload to register a new call (used by /ingest)."""

    source_uri: str = Field(
        ...,
        description="Either s3://bucket/key, minio://bucket/key, or a local file path.",
    )
    is_transcript: bool = Field(
        default=False,
        description="If true, source_uri points to a transcript file, not audio.",
    )
    metadata: CallMetadata = Field(default_factory=CallMetadata)


class CallRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_uri: str
    is_transcript: bool
    status: CallStatus
    detected_language: Language | None = None
    duration_seconds: float | None = None
    created_at: datetime
    updated_at: datetime
    metadata: CallMetadata
    langsmith_trace_id: str | None = None


# ============================================================================
# Utterances
# ============================================================================
class UtteranceSchema(BaseModel):
    """A single diarized + transcribed segment of speech."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    call_id: UUID
    speaker: Speaker
    start_ts: float = Field(..., description="Seconds from call start.")
    end_ts: float
    text: str
    language: Language
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    words: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional word-level timing/confidence from Whisper.",
    )


# ============================================================================
# Extracted questions / intents
# ============================================================================
class ExtractedQuestion(BaseModel):
    """One customer-side query distilled by the LLM extractor."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    call_id: UUID
    utterance_id: UUID | None = None

    raw_text: str = Field(..., description="Original customer utterance excerpt.")
    normalized_text: str = Field(
        ...,
        description="Standalone canonical phrasing in the original language.",
    )
    english_gloss: str | None = Field(
        default=None,
        description="Optional English paraphrase for cross-lingual search.",
    )

    question_type: QuestionType = QuestionType.QUESTION
    intent: Intent = Intent.OTHER
    secondary_intents: list[Intent] = Field(default_factory=list)

    language: Language
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_at: datetime | None = None


class ExtractionResult(BaseModel):
    """Wrapper returned by the LLM extraction service for a single call."""

    call_id: UUID
    questions: list[ExtractedQuestion]
    used_model: str
    raw_response: str | None = None  # for audit


# ============================================================================
# Embeddings
# ============================================================================
class EmbeddingRecord(BaseModel):
    """One embedding vector keyed back to its source."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    question_id: UUID
    model: str
    dim: int
    vector: list[float] = Field(..., description="L2-normalized; length == dim.")
    created_at: datetime | None = None


# ============================================================================
# Clusters
# ============================================================================
class ClusterRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None = None
    canonical_question: str | None = None
    centroid: list[float]
    dominant_language: Language
    dominant_intents: list[Intent]
    frequency: int = 0
    last_updated: datetime
    representative_question_ids: list[UUID] = Field(default_factory=list)
    is_stable: bool = True


class ClusterMember(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cluster_id: UUID
    question_id: UUID
    similarity: float
    assigned_at: datetime


class ClusterDetail(BaseModel):
    """Returned by GET /cluster/{id}."""

    cluster: ClusterRecord
    canonical_faq: "CanonicalFAQ | None" = None
    examples: list[ExtractedQuestion]
    intent_distribution: dict[Intent, int]
    language_distribution: dict[Language, int]


# ============================================================================
# Canonical FAQs
# ============================================================================
class CanonicalFAQ(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cluster_id: UUID
    canonical_question: str
    canonical_question_en: str | None = None
    suggested_answer: str | None = None
    language: Language
    confidence: float = 0.0
    version: int = 1
    created_at: datetime
    updated_at: datetime


# ============================================================================
# Memory graph edges
# ============================================================================
class MemoryEdge(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    source_cluster_id: UUID
    target_cluster_id: UUID
    relation: EdgeRelation
    weight: float = Field(..., ge=0.0, le=1.0)
    reason: str | None = None
    created_at: datetime | None = None


class MemoryGraph(BaseModel):
    """Bundle returned by GET /memory-graph."""

    nodes: list[ClusterRecord]
    edges: list[MemoryEdge]


# ============================================================================
# Feedback
# ============================================================================
class FeedbackAnnotation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    action: FeedbackAction
    payload: dict[str, Any]
    author: str | None = None
    note: str | None = None
    created_at: datetime | None = None


# ============================================================================
# Search / retrieval
# ============================================================================
class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    language: Language | None = None
    intents: list[Intent] | None = None
    min_score: float = 0.0


class SearchHit(BaseModel):
    question: ExtractedQuestion
    cluster_id: UUID | None
    score: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    cluster_aggregates: list[dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Analytics
# ============================================================================
class AnalyticsSummary(BaseModel):
    total_calls: int
    total_questions: int
    total_clusters: int
    language_distribution: dict[Language, int]
    intent_distribution: dict[Intent, int]
    top_clusters: list[dict[str, Any]]
    cluster_growth: list[dict[str, Any]]  # {date, new_clusters, churned_clusters}
    emerging_topics: list[dict[str, Any]]  # for drift detection
