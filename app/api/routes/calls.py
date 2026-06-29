"""
GET /calls/{id}              — Call metadata + status
GET /calls/{id}/utterances   — Speaker-labeled segments
GET /calls/{id}/questions    — Extracted questions
GET /calls/{id}/detail       — Unified analysis
GET /calls/{id}/transcription — Transcript + audio URL
GET /calls/{id}/analysis     — Sentiment, disposition, lead info
GET /calls/{id}/waveform     — Waveform visualization
GET /calls/stats             — Summary statistics
GET /calls                   — List with filter + search
POST /calls/{id}/redact-pii  — PII masking

These are simple read-models used by the Call Inspector view in the
dashboard. ORM rows are converted to Pydantic via `from_attributes`.
"""

from __future__ import annotations

import re
import structlog
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import cast, func, or_, select, String as SQLString
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db, get_llm_client
from app.api.errors import APIError
from app.services.extraction.call_analysis import CallAnalyzer
from app.models.enums import SentimentLabel
from app.models.schemas import (
    CallAnalysisResponse,
    CallDetailResponse,
    CallListItem,
    CallListResponse,
    CallMetadata,
    CallRead,
    CallStatsResponse,
    ExtractedQuestion,
    PIISegment,
    PIISummary,
    RedactRequest,
    RedactResponse,
    SentimentBreakdown,
    TranscriptionResponse,
    TranscriptSegment,
    TranscriptSegmentDetail,
    UtteranceSchema,
    Violation,
    WaveformBar,
    WaveformResponse,
)

router = APIRouter(prefix="/calls", tags=["calls"])


@router.get("/stats", response_model=CallStatsResponse)
async def get_call_stats(db: AsyncSession = Depends(get_db)) -> CallStatsResponse:
    """Get summary statistics for the call list header.

    Returns:
    - total: Total number of calls
    - avg_risk: Average risk score (0-100)
    - avg_confidence: Average confidence % (0-100)
    - flagged_percent: Percentage of calls with violations (0-100)
    """
    from app.db.models import Call

    # Get total count
    total = int((await db.execute(select(func.count(Call.id)))).scalar_one() or 0)

    if total == 0:
        return CallStatsResponse(total=0, avg_risk=50.0, avg_confidence=75.0, flagged_percent=0.0)

    # Stub: Calculate aggregated stats
    # For now:
    # - avg_risk = 50 (no risk data in DB)
    # - avg_confidence = 75 (no confidence data in DB)
    # - flagged_percent = 0 (no violations in DB)
    avg_risk = 50.0
    avg_confidence = 75.0
    flagged_count = 0  # Stub: no flagged calls yet
    flagged_percent = (flagged_count / total * 100) if total > 0 else 0.0

    return CallStatsResponse(
        total=total,
        avg_risk=avg_risk,
        avg_confidence=avg_confidence,
        flagged_percent=flagged_percent,
    )


@router.get("/{call_id}", response_model=CallRead)
async def get_call(call_id: UUID, db: AsyncSession = Depends(get_db)) -> CallRead:
    from app.db.models import Call

    row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    metadata = CallMetadata.model_validate(row.call_metadata or {})
    return CallRead(
        id=row.id,
        source_uri=row.source_uri,
        is_transcript=row.is_transcript,
        status=row.status,
        detected_language=row.detected_language,
        duration_seconds=row.duration_seconds,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=metadata,
        langsmith_trace_id=row.langsmith_trace_id,
        error_message=row.error_message,
    )


@router.get("/{call_id}/utterances", response_model=list[UtteranceSchema])
async def list_utterances(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[UtteranceSchema]:
    from app.db.models import Utterance

    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [UtteranceSchema.model_validate(r) for r in rows]


@router.get("/{call_id}/questions", response_model=list[ExtractedQuestion])
async def list_questions(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ExtractedQuestion]:
    from app.db.models import ExtractedQuestionORM

    stmt = (
        select(ExtractedQuestionORM)
        .where(ExtractedQuestionORM.call_id == call_id)
        .order_by(ExtractedQuestionORM.extracted_at.asc().nullsfirst())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [ExtractedQuestion.model_validate(r) for r in rows]


@router.get("/{call_id}/transcription", response_model=TranscriptionResponse)
async def get_transcription(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """Fetch transcription with speaker-labeled timing for audio playback sync.

    Returns:
    - audio_url: Presigned MinIO URL (valid 7 days)
    - transcript_with_timing: List of speaker segments with start/end timestamps
    - language, duration_seconds: Call metadata
    """
    from app.db.models import Call, Utterance

    # Fetch call metadata
    call_row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call_row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    # Fetch utterances (already ordered by start_ts in existing endpoint)
    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    utterances = (await db.execute(stmt)).scalars().all()

    # Build transcript with timing
    segments = [
        TranscriptSegment(
            speaker=u.speaker,
            text=u.text,
            start_ts=u.start_ts,
            end_ts=u.end_ts,
        )
        for u in utterances
    ]

    # Generate presigned URL for audio (if not a transcript-only call)
    audio_url = ""
    if not call_row.is_transcript and call_row.source_uri:
        try:
            audio_url = _generate_presigned_url(call_row.source_uri)
        except Exception as exc:  # noqa: BLE001
            log = structlog.get_logger(__name__)
            log.warning(
                "presigned_url_generation_failed",
                call_id=str(call_id),
                source_uri=call_row.source_uri,
                error=str(exc),
            )
            # Continue with empty URL rather than failing the entire response

    return TranscriptionResponse(
        call_id=call_id,
        audio_url=audio_url,
        transcript_with_timing=segments,
        language=call_row.detected_language,
        duration_seconds=call_row.duration_seconds,
    )


@router.get("/{call_id}/analysis", response_model=CallAnalysisResponse)
async def get_analysis(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> CallAnalysisResponse:
    """Unified call analysis: sentiment, disposition, lead info, quality metrics.

    Fetches utterances and runs the CallAnalyzer on-demand if not cached.
    Computes derived metrics like quality_score and keywords.

    Returns:
    - sentiment, disposition, lead info
    - keywords from extracted questions
    - quality_score based on sentiment + disposition + escalation
    """
    from app.db.models import Call, Utterance, ExtractedQuestionORM

    # Fetch call
    call_row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call_row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    # Fetch utterances
    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    utterance_rows = (await db.execute(stmt)).scalars().all()
    utterances = [UtteranceSchema.model_validate(u, from_attributes=True) for u in utterance_rows]

    # Run CallAnalyzer
    llm_client = get_llm_client()
    analyzer = CallAnalyzer(llm_client)
    analysis_result = await analyzer.analyze(call_id, utterances)

    # Extract top intents from questions (for keywords / intent derivation)
    q_stmt = (
        select(ExtractedQuestionORM)
        .where(ExtractedQuestionORM.call_id == call_id)
        .order_by(ExtractedQuestionORM.confidence.desc())
        .limit(10)
    )
    questions = (await db.execute(q_stmt)).scalars().all()

    # Derive keywords and primary intent
    keywords = []
    primary_intent = None
    secondary_intents_set = set()
    intent_counts: dict[str, int] = {}
    for q in questions:
        keywords.append(q.normalized_text[:50])  # Truncate to 50 chars
        if q.intent:
            intent_counts[q.intent.value] = intent_counts.get(q.intent.value, 0) + 1
            if primary_intent is None:
                primary_intent = q.intent
            else:
                secondary_intents_set.add(q.intent)
        # Add secondary intents from question metadata
        for sec_intent_str in (q.secondary_intents or []):
            try:
                from app.models.enums import Intent
                secondary_intents_set.add(Intent(sec_intent_str))
            except (ValueError, TypeError):
                pass

    secondary_intents_list = list(secondary_intents_set)[:2]  # Top 2 secondary

    # Compute quality_score: weighted combination
    # High sentiment + positive disposition = high quality
    # Escalation reduces quality
    base_quality = (analysis_result.analysis.sentiment_confidence * 0.4 +
                    analysis_result.analysis.disposition_confidence * 0.4)
    if analysis_result.analysis.escalation:
        base_quality *= 0.6
    quality_score = max(0.0, min(1.0, base_quality))

    call_metadata = CallMetadata.model_validate(call_row.call_metadata or {})

    return CallAnalysisResponse(
        call_id=call_id,
        sentiment=analysis_result.analysis.sentiment,
        sentiment_confidence=analysis_result.analysis.sentiment_confidence,
        disposition=analysis_result.analysis.disposition,
        disposition_confidence=analysis_result.analysis.disposition_confidence,
        disposition_rationale=analysis_result.analysis.disposition_rationale,
        intent=primary_intent,
        secondary_intents=secondary_intents_list,
        escalation=analysis_result.analysis.escalation,
        lead=analysis_result.analysis.lead,
        keywords=keywords[:5],  # Top 5 keywords
        quality_score=quality_score,
        call_metadata=call_metadata,
        language=call_row.detected_language,
        duration_seconds=call_row.duration_seconds,
    )


@router.post("/{call_id}/redact-pii", response_model=RedactResponse)
async def redact_pii(
    call_id: UUID,
    payload: RedactRequest,
    db: AsyncSession = Depends(get_db),
) -> RedactResponse:
    """Mask or remove PII entities from call transcription.

    Detects common PII patterns (SSN, PHONE, EMAIL, etc.) and replaces them
    according to redaction_method:
    - "mask": Replace with X's or * (e.g., "***-**-1234" for SSN)
    - "remove": Delete entirely

    Returns:
    - redacted_transcript: Full transcript with PII redacted
    - pii_segments: List of detected PII with locations
    - pii_summary: Count by type
    """
    from app.db.models import Call, Utterance

    # Fetch call
    call_row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call_row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    # Fetch and concatenate all utterances into one transcript
    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    utterance_rows = (await db.execute(stmt)).scalars().all()

    # Build full transcript
    full_transcript = " ".join(u.text for u in utterance_rows)

    # Detect PII
    pii_segments, summary = _detect_and_redact_pii(full_transcript, payload.redaction_method)

    # Build redacted transcript
    redacted = full_transcript
    # Sort by end_idx descending to maintain indices while replacing
    for segment in sorted(pii_segments, key=lambda s: s.end_idx, reverse=True):
        redacted = redacted[: segment.start_idx] + segment.replacement + redacted[segment.end_idx :]

    return RedactResponse(
        call_id=call_id,
        redacted_transcript=redacted,
        pii_segments=pii_segments,
        pii_summary=summary,
        redaction_method=payload.redaction_method,
    )


# ============================================================================
# Aether Flow: Call List, Detail, Waveform, Stats
# ============================================================================
@router.get("", response_model=CallListResponse)
async def list_calls(
    filter: str = "all",
    search: str = "",
    db: AsyncSession = Depends(get_db),
) -> CallListResponse:
    """Fetch calls for the Call List view.

    Query parameters:
    - filter: "all" (default), "flagged" (violation_count > 0), "clean" (violation_count == 0)
    - search: Search across call ID, agent name, customer name (case-insensitive)

    Returns list of CallListItem with essential metadata for list display.
    """
    from app.db.models import Call

    # Build base query
    stmt = select(Call)

    # Apply search filter if provided
    if search:
        search_lower = search.lower()
        # Search in call ID, agent_id (from metadata), or customer_id (from metadata)
        stmt = stmt.where(
            or_(
                cast(Call.id, SQLString).ilike(f"%{search_lower}%"),
                Call.call_metadata["agent_id"].astext.ilike(f"%{search_lower}%"),
                Call.call_metadata["customer_id"].astext.ilike(f"%{search_lower}%"),
            )
        )

    # Fetch all matching calls
    rows = (await db.execute(stmt.order_by(Call.created_at.desc()))).scalars().all()

    # Convert to CallListItem and apply filter
    items: list[CallListItem] = []
    for row in rows:
        metadata = CallMetadata.model_validate(row.call_metadata or {})

        # Stub: Assume risk_score based on sentiment (to be derived from analysis later)
        # For now, use a placeholder of 50
        risk_score = 50

        # Stub: violation_count = 0 (PII logic not yet implemented)
        violation_count = 0

        # Stub: sentiment = NEUTRAL (to be fetched from CallAnalyzer if needed)
        sentiment = SentimentLabel.NEUTRAL

        item = CallListItem(
            id=row.id,
            agent_name=metadata.agent_id,
            customer_name=metadata.customer_id,
            duration_seconds=row.duration_seconds,
            sentiment=sentiment,
            risk_score=risk_score,
            violation_count=violation_count,
        )

        # Apply filter
        if filter == "flagged" and violation_count == 0:
            continue
        elif filter == "clean" and violation_count > 0:
            continue

        items.append(item)

    return CallListResponse(calls=items)


@router.get("/{call_id}/detail", response_model=CallDetailResponse)
async def get_call_detail(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> CallDetailResponse:
    """Fetch unified call detail for the Call Detail view.

    Merges data from calls, utterances, and analysis to provide:
    - Basic metadata (id, agent, customer, dates)
    - Risk score, risk level, confidence
    - Sentiment breakdown
    - Violations list (stubbed for now)
    - Full transcript with flagged segments
    - Presigned audio URL
    """
    from app.db.models import Call, Utterance

    # Fetch call
    call_row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call_row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    metadata = CallMetadata.model_validate(call_row.call_metadata or {})

    # Fetch utterances
    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    utterance_rows = (await db.execute(stmt)).scalars().all()

    # Build transcript with flagged segments
    transcript: list[TranscriptSegmentDetail] = []
    for u in utterance_rows:
        segment = TranscriptSegmentDetail(
            time_start=u.start_ts,
            time_end=u.end_ts,
            speaker=u.speaker,
            text=u.text,
            flagged=False,  # Stub: no flagging logic yet
        )
        transcript.append(segment)

    # Generate presigned URL
    audio_url = ""
    if not call_row.is_transcript and call_row.source_uri:
        try:
            audio_url = _generate_presigned_url(call_row.source_uri)
        except Exception:  # noqa: BLE001
            pass

    # Stub: risk_score, risk_level, confidence, sentiment breakdown
    risk_score = 50
    risk_level = "MEDIUM"
    confidence = 75.0
    tone = None
    sentiment_breakdown = SentimentBreakdown(negative=20.0, neutral=50.0, positive=30.0)

    # Stub: violations list (empty per constraints)
    violations: list[Violation] = []
    violation_count = 0

    # Stub: summary (could be derived from CallAnalyzer)
    summary = None

    return CallDetailResponse(
        id=call_row.id,
        agent_name=metadata.agent_id,
        customer_name=metadata.customer_id,
        date=call_row.created_at,
        duration=call_row.duration_seconds,
        risk_score=risk_score,
        risk_level=risk_level,
        confidence=confidence,
        tone=tone,
        violation_count=violation_count,
        sentiment=sentiment_breakdown,
        summary=summary,
        violations=violations,
        transcript=transcript,
        audio_url=audio_url,
    )


@router.get("/{call_id}/waveform", response_model=WaveformResponse)
async def get_waveform(
    call_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> WaveformResponse:
    """Generate synthetic waveform visualization for audio player.

    Creates 72 bars based on utterance distribution and energy.
    Marks flagged segments if violations detected.

    Bar height (1-30) is derived from:
    - Utterance frequency in time bucket
    - Utterance confidence / energy
    """
    from app.db.models import Call, Utterance

    # Fetch call to validate it exists
    call_row = (await db.execute(select(Call).where(Call.id == call_id))).scalar_one_or_none()
    if call_row is None:
        raise APIError(
            f"Call {call_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="call_not_found",
        )

    duration = call_row.duration_seconds or 1.0
    if duration <= 0:
        duration = 1.0

    # Fetch utterances
    stmt = (
        select(Utterance)
        .where(Utterance.call_id == call_id)
        .order_by(Utterance.start_ts.asc())
    )
    utterance_rows = (await db.execute(stmt)).scalars().all()

    # Generate 72 bars
    num_bars = 72
    bar_duration = duration / num_bars
    bars: list[WaveformBar] = []

    for i in range(num_bars):
        bar_start = i * bar_duration
        bar_end = (i + 1) * bar_duration

        # Find utterances in this time bucket
        utterances_in_bar = [
            u for u in utterance_rows
            if u.start_ts < bar_end and u.end_ts > bar_start
        ]

        # Calculate bar height based on utterance count and confidence
        if utterances_in_bar:
            avg_confidence = sum(u.confidence for u in utterances_in_bar) / len(utterances_in_bar)
            utterance_count = len(utterances_in_bar)
            # Height: 1-30, with more utterances and higher confidence yielding higher bars
            height = min(30, max(1, int(1 + (avg_confidence * 15) + (utterance_count * 3))))
        else:
            height = 1

        # Stub: No flagged segments yet
        flagged = False

        bar = WaveformBar(height=height, flagged=flagged, played=False)
        bars.append(bar)

    return WaveformResponse(call_id=call_id, bars=bars)


# ============================================================================
# Helpers
# ============================================================================
def _generate_presigned_url(source_uri: str, expiration_days: int = 7) -> str:
    """Generate a presigned MinIO URL for audio playback.

    Args:
        source_uri: minio://bucket/key or s3://bucket/key
        expiration_days: How long the URL is valid (default 7 days)

    Returns:
        Presigned HTTPS URL string, or empty string if generation fails.
    """
    from urllib.parse import urlparse
    from app.services.audio.io import _get_minio_client, _split_bucket_key

    try:
        parsed = urlparse(source_uri)
        scheme = (parsed.scheme or "").lower()

        if scheme not in ("minio", "s3"):
            return ""

        bucket, key = _split_bucket_key(parsed.netloc, parsed.path)
        client = _get_minio_client()

        # Generate presigned URL valid for 7 days (604800 seconds)
        url = client.get_presigned_download_url(
            bucket_name=bucket,
            object_name=key,
            expires=expiration_days * 86400,
        )
        return url
    except Exception:  # noqa: BLE001
        return ""


def _detect_and_redact_pii(
    text: str,
    method: str = "mask",
) -> tuple[list[PIISegment], PIISummary]:
    """Detect common PII patterns and generate redaction segments.

    Supports:
    - SSN: 123-45-6789, 123456789, 12345-6789
    - PHONE: (123) 456-7890, 123-456-7890, 1234567890 (10-digit)
    - EMAIL: user@example.com
    - AADHAR: 12-digit with optional dashes
    - PAN: 10-char alphanumeric (Indian tax ID)

    Args:
        text: Full transcript to scan
        method: "mask" or "remove"

    Returns:
        Tuple of (pii_segments list, PIISummary)
    """
    segments: list[PIISegment] = []
    count_by_type: dict[str, int] = {}

    # SSN patterns: ###-##-#### or ########## or #####-####
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b|\b\d{5}-\d{4}\b"
    for match in re.finditer(ssn_pattern, text):
        pii_type = "SSN"
        count_by_type[pii_type] = count_by_type.get(pii_type, 0) + 1
        replacement = "***-**-" + match.group()[-4:] if method == "mask" else ""
        segments.append(
            PIISegment(
                type=pii_type,
                value=match.group(),
                start_idx=match.start(),
                end_idx=match.end(),
                replacement=replacement,
            )
        )

    # PHONE patterns: (123) 456-7890, 123-456-7890, 1234567890
    # Avoid matching SSN already detected
    phone_pattern = r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|\b\d{10}\b"
    ssn_indices = {(s.start_idx, s.end_idx) for s in segments if s.type == "SSN"}
    for match in re.finditer(phone_pattern, text):
        # Skip if already covered by SSN detection
        if any(start <= match.start() < end or start < match.end() <= end
               for start, end in ssn_indices):
            continue
        pii_type = "PHONE"
        count_by_type[pii_type] = count_by_type.get(pii_type, 0) + 1
        replacement = "***-***-" + match.group()[-4:] if method == "mask" else ""
        segments.append(
            PIISegment(
                type=pii_type,
                value=match.group(),
                start_idx=match.start(),
                end_idx=match.end(),
                replacement=replacement,
            )
        )

    # EMAIL: user@example.com
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    for match in re.finditer(email_pattern, text):
        pii_type = "EMAIL"
        count_by_type[pii_type] = count_by_type.get(pii_type, 0) + 1
        if method == "mask":
            local, domain = match.group().split("@")
            replacement = local[0] + "***@" + domain
        else:
            replacement = ""
        segments.append(
            PIISegment(
                type=pii_type,
                value=match.group(),
                start_idx=match.start(),
                end_idx=match.end(),
                replacement=replacement,
            )
        )

    # AADHAR: 12-digit with optional dashes (####-####-####)
    aadhar_pattern = r"\b\d{4}-\d{4}-\d{4}\b|\b\d{12}\b"
    for match in re.finditer(aadhar_pattern, text):
        pii_type = "AADHAR"
        count_by_type[pii_type] = count_by_type.get(pii_type, 0) + 1
        replacement = "****-****-" + match.group()[-4:] if method == "mask" else ""
        segments.append(
            PIISegment(
                type=pii_type,
                value=match.group(),
                start_idx=match.start(),
                end_idx=match.end(),
                replacement=replacement,
            )
        )

    # PAN: 10-char alphanumeric (e.g., ABCDE1234F)
    pan_pattern = r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"
    for match in re.finditer(pan_pattern, text):
        pii_type = "PAN"
        count_by_type[pii_type] = count_by_type.get(pii_type, 0) + 1
        replacement = match.group()[:2] + "***" + match.group()[-2:] if method == "mask" else ""
        segments.append(
            PIISegment(
                type=pii_type,
                value=match.group(),
                start_idx=match.start(),
                end_idx=match.end(),
                replacement=replacement,
            )
        )

    # Deduplicate overlapping segments (keep first occurrence of each position)
    sorted_segments = sorted(segments, key=lambda s: (s.start_idx, s.end_idx))
    unique_segments: list[PIISegment] = []
    last_end = -1
    for seg in sorted_segments:
        if seg.start_idx >= last_end:
            unique_segments.append(seg)
            last_end = seg.end_idx

    summary = PIISummary(
        count_by_type=count_by_type,
        total_count=sum(count_by_type.values()),
    )

    return unique_segments, summary
