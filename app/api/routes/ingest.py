"""
POST /ingest — register a new call and enqueue the pipeline.

This endpoint is intentionally minimal: write a `calls` row with status
PENDING, dispatch `v2t.ingest` by name, and return 202 immediately. The
heavy lifting (STT -> diarize -> extract -> embed -> cluster) happens in
Celery workers.

Idempotency: callers may send an ``Idempotency-Key`` header. The first
successful call mints a call_id and stores (header, call_id) in Redis for
24h. Retries with the same header return the original call_id with HTTP 200
instead of creating a duplicate row.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import idempotency
from app.api.celery_sender import send
from app.api.dependencies import get_db
from app.api.errors import APIError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import calls_ingested
from app.models.enums import CallStatus
from app.models.schemas import CallCreate, CallMetadata
from app.services.audio.io import save_upload

logger = get_logger(__name__)
router = APIRouter(tags=["ingest"])

# Extension -> is_transcript. Drives type inference for /ingest/upload.
_AUDIO_EXTS = frozenset({".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"})
_TRANSCRIPT_EXTS = frozenset({".json"})

# Streaming read size for the upload temp-file copy.
_CHUNK_SIZE = 1024 * 1024

_DEFAULT_UPLOAD_NAME = "upload"


async def _create_and_dispatch(
    db: AsyncSession,
    *,
    source_uri: str,
    is_transcript: bool,
    metadata: CallMetadata,
) -> str:
    """Write a PENDING ``calls`` row and dispatch ``v2t.ingest``.

    Shared by the JSON ``/ingest`` and multipart ``/ingest/upload`` handlers.
    Returns the new ``call_id`` (str).
    """

    from app.db.models import Call

    call = Call(
        source_uri=source_uri,
        is_transcript=is_transcript,
        status=CallStatus.PENDING,
        call_metadata=metadata.model_dump(mode="json"),
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
    logger.info("call_ingested", call_id=call_id, source_uri=source_uri)
    return call_id


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_call(
    payload: CallCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, str]:
    """Create the call row and queue the pipeline. Idempotent on Idempotency-Key."""

    # ---- Replay on duplicate Idempotency-Key ----
    existing = await idempotency.lookup("ingest", idempotency_key)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        logger.info(
            "ingest_idempotent_replay",
            call_id=existing,
            idempotency_key=idempotency_key,
        )
        return {"call_id": existing, "replay": "true"}

    call_id = await _create_and_dispatch(
        db,
        source_uri=payload.source_uri,
        is_transcript=payload.is_transcript,
        metadata=payload.metadata,
    )

    # Record the (key -> call_id) mapping AFTER the dispatch succeeds so a
    # client retry on a half-failed request still creates a fresh call.
    await idempotency.remember("ingest", idempotency_key, call_id)

    logger.info(
        "ingest_idempotency_recorded",
        call_id=call_id,
        idempotency_key=idempotency_key,
    )
    return {"call_id": call_id}


def _resolve_upload_type(
    ext: str, is_transcript_override: bool | None
) -> tuple[bool, str]:
    """Infer (is_transcript, bucket) from the file extension.

    ``is_transcript_override`` (from the form field) wins over inference but
    only after the extension itself is recognized as supported.
    """
    settings = get_settings()
    ext = ext.lower()
    if ext in _TRANSCRIPT_EXTS:
        inferred = True
    elif ext in _AUDIO_EXTS:
        inferred = False
    else:
        raise APIError(
            f"Unsupported file extension: {ext or '(none)'!r}.",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            error_type="unsupported_media_type",
        )

    is_transcript = inferred if is_transcript_override is None else is_transcript_override
    bucket = (
        settings.minio_bucket_transcripts
        if is_transcript
        else settings.minio_bucket_audio
    )
    return is_transcript, bucket


def _safe_name(filename: str | None) -> str:
    """Strip path separators / traversal from an uploaded filename."""
    if not filename:
        return _DEFAULT_UPLOAD_NAME
    # PurePath().name drops any directory components and ../ traversal.
    name = PurePath(filename.replace("\\", "/")).name
    name = name.strip()
    return name or _DEFAULT_UPLOAD_NAME


@router.post("/ingest/upload", status_code=status.HTTP_202_ACCEPTED)
async def ingest_upload(
    response: Response,
    file: UploadFile = File(...),
    campaign: str | None = Form(None),
    channel: str | None = Form(None),
    is_transcript: bool | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Accept a multipart audio/transcript upload, store it in MinIO, ingest it.

    Type is inferred from the file extension (``.json`` -> transcript;
    ``.wav/.mp3/.m4a/.ogg/.flac/.webm`` -> audio) and may be overridden by the
    ``is_transcript`` form field. The file is streamed to a temp file with a
    size cap, uploaded to the appropriate bucket, and the standard pipeline is
    dispatched. Returns 202 with the new call_id and the MinIO source_uri.
    """
    settings = get_settings()
    safe_name = _safe_name(file.filename)
    ext = PurePath(safe_name).suffix
    resolved_is_transcript, bucket = _resolve_upload_type(ext, is_transcript)

    max_bytes = settings.upload_max_mb * 1024 * 1024
    tmp_path: str | None = None
    total = 0
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="v2t_upload_", suffix=ext or ".bin")
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise APIError(
                        f"Uploaded file exceeds the {settings.upload_max_mb} MiB limit.",
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        error_type="file_too_large",
                    )
                out.write(chunk)

        if total == 0:
            raise APIError(
                "Uploaded file is empty.",
                status_code=status.HTTP_400_BAD_REQUEST,
                error_type="empty_file",
            )

        key = f"{uuid4()}/{safe_name}"
        try:
            source_uri = save_upload(tmp_path, bucket=bucket, key=key)
        except APIError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload_storage_failed", bucket=bucket, key=key, error=str(exc))
            raise APIError(
                "Failed to store the uploaded file.",
                status_code=status.HTTP_502_BAD_GATEWAY,
                error_type="upload_storage_failed",
            ) from exc

        metadata = CallMetadata(campaign=campaign, channel=channel)
        call_id = await _create_and_dispatch(
            db,
            source_uri=source_uri,
            is_transcript=resolved_is_transcript,
            metadata=metadata,
        )
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:  # pragma: no cover - best-effort cleanup
                pass

    logger.info(
        "call_uploaded",
        call_id=call_id,
        source_uri=source_uri,
        is_transcript=resolved_is_transcript,
        bytes=total,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return {
        "call_id": call_id,
        "source_uri": source_uri,
        "is_transcript": resolved_is_transcript,
    }
