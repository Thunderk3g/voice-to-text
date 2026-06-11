"""
GET /calls/{id}, /calls/{id}/utterances, /calls/{id}/questions.

These are simple read-models used by the Call Inspector view in the
dashboard. ORM rows are converted to Pydantic via `from_attributes`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.errors import APIError
from app.models.schemas import (
    CallMetadata,
    CallRead,
    ExtractedQuestion,
    UtteranceSchema,
)

router = APIRouter(prefix="/calls", tags=["calls"])


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
