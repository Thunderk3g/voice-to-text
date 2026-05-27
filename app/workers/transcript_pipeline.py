"""
Transcript fast-path.

When a call is registered with `is_transcript=True`, we skip STT + diarization
and load utterances directly from a transcript JSON via the transcript_loader
service. The Celery `start_call_pipeline` chain then jumps straight to
`v2t.extract`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from app.core.logging import get_logger
from app.models.enums import CallStatus
from app.workers.cluster_glue import insert_utterances, set_call_status
from app.workers.db import sync_session
from app.workers.run_async import run_async

log = get_logger("v2t.workers.transcript")


def _coerce_utterance_row(call_id: str, u: Any) -> dict[str, Any]:
    """Normalize a single utterance into the row shape `utterances` expects."""
    if hasattr(u, "model_dump"):
        d = u.model_dump()
    elif isinstance(u, dict):
        d = dict(u)
    else:
        raise TypeError(f"Unsupported utterance type: {type(u)!r}")

    return {
        "call_id": call_id,
        "speaker": str(d.get("speaker", "UNKNOWN")),
        "start_ts": float(d.get("start_ts", 0.0)),
        "end_ts": float(d.get("end_ts", 0.0)),
        "text": d.get("text", ""),
        "language": str(d.get("language", "en")),
        "confidence": float(d.get("confidence", 0.0)),
        "words": d.get("words"),
    }


def load_transcript_to_utterances(call_id: str | UUID) -> int:
    """Load a transcript file via the transcript_loader service and persist.

    Returns the number of utterances written. Sets the call status to
    `DIARIZATION_DONE` so the rest of the pipeline can pick up from there.
    """
    cid = str(call_id)
    structlog.contextvars.bind_contextvars(call_id=cid)
    log.info("transcript_load_start")

    # Import lazily so this module is importable in environments where the
    # STT service is not installed (e.g. CPU-only worker images).
    from app.services.stt import transcript_loader

    with sync_session() as session:
        # Fetch the source URI from the calls table.
        row = session.execute(
            __import__("sqlalchemy").text(
                "SELECT source_uri FROM calls WHERE id = :cid"
            ),
            {"cid": cid},
        ).mappings().first()
        if row is None:
            raise ValueError(f"call_id {cid} not found")
        source_uri = row["source_uri"]

        # transcript_loader.load_transcript(call_id, json_path) is sync.
        loader = getattr(transcript_loader, "load_transcript", None) or getattr(
            transcript_loader, "load", None
        )
        if loader is None:
            raise RuntimeError(
                "app.services.stt.transcript_loader must expose load_transcript()"
            )

        # Resolve any storage URI (file://, minio://, s3://) to a local path.
        from app.services.audio.io import cleanup_temp, download_to_temp

        local_path = download_to_temp(source_uri)
        try:
            result = loader(UUID(cid), local_path)
            if hasattr(result, "__await__"):
                utterances = run_async(result)
            else:
                utterances = result
        finally:
            cleanup_temp(local_path)

        rows = [_coerce_utterance_row(cid, u) for u in utterances]
        insert_utterances(session, cid, rows)
        set_call_status(session, cid, CallStatus.DIARIZATION_DONE.value)

    log.info("transcript_load_done", utterance_count=len(rows))
    return len(rows)


__all__ = ["load_transcript_to_utterances"]
