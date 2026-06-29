"""
Pydantic schemas — the contract between API, workers, services, and DB.

These are *transport* models. SQLAlchemy ORM models live in app/db/models.py
and convert to/from these via `.from_orm()` / `model_validate()`.

DO NOT add business logic here — keep schemas declarative.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    CallDisposition,
    CallStatus,
    EdgeRelation,
    FeedbackAction,
    Intent,
    Language,
    QuestionType,
    SentimentLabel,
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
    stt_provider: Literal["sarvam", "whisper", "indic_conformer"] | None = None
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
    error_message: str | None = None


# ============================================================================
# Utterances
# ============================================================================
class UtteranceSchema(BaseModel):
    """A single diarized + transcribed segment of speech."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    call_id: UUID
    speaker: Speaker
    speaker_id: str | None = Field(
        default=None,
        description='Raw diarization speaker label (e.g. "0", "1") before AGENT/CUSTOMER mapping.',
    )
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
# Per-call analysis (lead + disposition + sentiment)
# ============================================================================
class Lead(BaseModel):
    """Lead attributes distilled from a call. Every field optional/grounded."""

    full_name: str | None = None
    phone: str | None = Field(default=None, description="Normalized 10-digit mobile (join key).")
    email: str | None = None
    age: int | None = None
    gender: str | None = None
    occupation: str | None = None
    education: str | None = None
    income_band: str | None = None
    pincode: str | None = None
    product_interest: str | None = None
    policy_no: str | None = None
    callback_time: str | None = None
    grounded_fields: list[str] = Field(default_factory=list)


class CallAnalysis(BaseModel):
    lead: Lead = Field(default_factory=Lead)
    disposition: CallDisposition = CallDisposition.OTHER
    disposition_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disposition_rationale: str | None = None
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    sentiment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    escalation: bool = False


class CallAnalysisResult(BaseModel):
    call_id: UUID
    analysis: CallAnalysis
    used_model: str
    raw_response: str | None = None


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
# Transcription (audio playback support)
# ============================================================================
class TranscriptSegment(BaseModel):
    """One speaker utterance with precise timing."""

    speaker: Speaker
    text: str
    start_ts: float = Field(..., description="Seconds from call start.")
    end_ts: float = Field(..., description="Seconds from call start.")


class TranscriptionResponse(BaseModel):
    """Unified response for audio playback + text sync."""

    call_id: UUID
    audio_url: str = Field(..., description="MinIO presigned URL (expires in 7 days).")
    transcript_with_timing: list[TranscriptSegment]
    language: Language | None = None
    duration_seconds: float | None = None


# ============================================================================
# PII Redaction
# ============================================================================
class PIISegment(BaseModel):
    """One detected PII entity with location and replacement."""

    type: Literal["SSN", "PHONE", "EMAIL", "NAME", "POLICY_NO", "AADHAR", "PAN"] = Field(
        ..., description="Standardized PII type."
    )
    value: str = Field(..., description="Original detected value.")
    start_idx: int = Field(..., description="Character index in transcript where PII starts.")
    end_idx: int = Field(..., description="Character index where PII ends (exclusive).")
    replacement: str = Field(..., description="Masked/removed replacement (e.g., '***-**-1234' or '').")


class PIISummary(BaseModel):
    """Count of PII entities by type."""

    count_by_type: dict[str, int] = Field(
        default_factory=dict, description="e.g. {'SSN': 2, 'PHONE': 1, 'EMAIL': 1}"
    )
    total_count: int = 0


class RedactRequest(BaseModel):
    """Payload for PII redaction."""

    redaction_method: Literal["mask", "remove"] = Field(
        default="mask",
        description="'mask' replaces with X's or *, 'remove' deletes entirely.",
    )


class RedactResponse(BaseModel):
    """Result of PII redaction on full transcript."""

    call_id: UUID
    redacted_transcript: str
    pii_segments: list[PIISegment] = Field(default_factory=list)
    pii_summary: PIISummary = Field(default_factory=PIISummary)
    redaction_method: Literal["mask", "remove"]


# ============================================================================
# Unified call analysis
# ============================================================================
class CallAnalysisResponse(BaseModel):
    """Unified analysis response combining lead + disposition + sentiment + metadata."""

    call_id: UUID
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    sentiment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disposition: CallDisposition = CallDisposition.OTHER
    disposition_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disposition_rationale: str | None = None
    intent: Intent | None = None
    secondary_intents: list[Intent] = Field(default_factory=list)
    escalation: bool = False
    lead: Lead = Field(default_factory=Lead)
    keywords: list[str] = Field(
        default_factory=list,
        description="Top extracted keywords/topics from call (computed from questions).",
    )
    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall call quality (0-1), based on sentiment, disposition, and escalation.",
    )
    call_metadata: CallMetadata = Field(default_factory=CallMetadata)
    language: Language | None = None
    duration_seconds: float | None = None


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


# ============================================================================
# Call List (Aether Flow: Call List view)
# ============================================================================
class CallListItem(BaseModel):
    """Single call in the call list view."""

    id: UUID
    agent_name: str | None = None
    customer_name: str | None = None
    duration_seconds: float | None = None
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    risk_score: int = Field(default=50, ge=0, le=100, description="0-100 risk meter")
    violation_count: int = 0


class CallListResponse(BaseModel):
    """Response for GET /calls endpoint."""

    calls: list[CallListItem]


# ============================================================================
# Call Violations (Aether Flow: Call Detail view)
# ============================================================================
class Violation(BaseModel):
    """Single flagged violation in a call."""

    time: float = Field(..., description="Seconds from call start")
    title: str = Field(..., description="Violation title (e.g., 'Compliance Breach')")
    severity: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    quote: str = Field(..., description="Relevant transcript snippet")
    note: str | None = None


# ============================================================================
# Sentiment Breakdown (Aether Flow: Call Detail view)
# ============================================================================
class SentimentBreakdown(BaseModel):
    """Sentiment distribution across the call."""

    negative: float = Field(default=0.0, ge=0.0, le=100.0, description="Negative %")
    neutral: float = Field(default=50.0, ge=0.0, le=100.0, description="Neutral %")
    positive: float = Field(default=50.0, ge=0.0, le=100.0, description="Positive %")


# ============================================================================
# Call Detail (Aether Flow: Call Detail view)
# ============================================================================
class TranscriptSegmentDetail(BaseModel):
    """Transcript segment with flagged status for detail view."""

    time_start: float = Field(..., description="Seconds from call start")
    time_end: float = Field(..., description="Seconds from call start")
    speaker: Speaker
    text: str
    flagged: bool = False

    @field_validator('time_end')
    @classmethod
    def validate_time_range(cls, v, info):
        if info.data.get('time_start') and v < info.data['time_start']:
            raise ValueError('time_end must be >= time_start')
        return v


class CallDetailResponse(BaseModel):
    """Unified response for GET /calls/{id}/detail."""

    id: UUID
    agent_name: str | None = None
    customer_name: str | None = None
    date: datetime | None = None
    duration: float | None = None
    risk_score: int = Field(default=50, ge=0, le=100)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    confidence: float = Field(default=0.0, ge=0.0, le=100.0, description="Confidence %")
    tone: str | None = None
    violation_count: int = 0
    sentiment: SentimentBreakdown = Field(default_factory=SentimentBreakdown)
    summary: str | None = None
    violations: list[Violation] = Field(default_factory=list)
    transcript: list[TranscriptSegmentDetail] = Field(default_factory=list)
    audio_url: str = ""


# ============================================================================
# Waveform (Aether Flow: Audio player)
# ============================================================================
class WaveformBar(BaseModel):
    """Single bar in waveform visualization."""

    height: int = Field(default=5, ge=1, le=30, description="Bar height 1-30")
    flagged: bool = False
    played: bool = False


class WaveformResponse(BaseModel):
    """Response for GET /calls/{id}/waveform."""

    call_id: UUID
    bars: list[WaveformBar]


# ============================================================================
# Call Stats (Aether Flow: List header)
# ============================================================================
class CallStatsResponse(BaseModel):
    """Summary statistics for call list header."""

    total: int = 0
    avg_risk: float = Field(default=50.0, ge=0.0, le=100.0, description="Average risk score")
    avg_confidence: float = Field(default=75.0, ge=0.0, le=100.0, description="Average confidence %")
    flagged_percent: float = Field(default=20.0, ge=0.0, le=100.0, description="% of calls with violations")
