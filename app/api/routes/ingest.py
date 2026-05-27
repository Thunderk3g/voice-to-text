"""
POST /ingest — register a new call and enqueue the pipeline.

This endpoint is intentionally minimal: write a `calls` row with status
PENDING, dispatch `v2t.ingest` by name, and return 202 immediately. The
heavy lifting (STT → diarize → extract → embed → cluster) happens in
Celery workers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.celery_sender import send
from app.api.dependencies import get_db
from app.api.errors import APIError
from app.core.logging import get_logger
from app.core.observability import calls_ingested
from app.models.enums import CallStatus
from app.models.schemas import CallCreate

logger = get_logger(__name__)
router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_call(
    payload: CallCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Create the call row and queue the pipeline."""

    from app.db.models import Call

    call = Call(
        source_uri=payload.source_uri,
        is_transcript=payload.is_transcript,
        status=CallStatus.PENDING,
        meta=payload.metadata.model_dump(mode="json"),
    )

    try:
        db.add(call)
        await db.flush()
        call_id = str(call.id)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("ingest_db_write_failed", error=str(exc))
        raise APIError(
            "Failed to persist call.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="ingest_persist_failed",
        ) from exc

    try:
        send("v2t.ingest", call_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest_celery_dispatch_failed", call_id=call_id, error=str(exc))
        raise APIError(
            "Call stored but pipeline dispatch failed.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_type="ingest_dispatch_failed",
        ) from exc

    calls_ingested.inc()
    logger.info("call_ingested", call_id=call_id, source_uri=payload.source_uri)
    return {"call_id": call_id}
