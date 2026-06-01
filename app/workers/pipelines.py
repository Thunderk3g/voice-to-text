"""
Pipeline composition — turns a call_id into a Celery `chain` of tasks.

Two entry shapes:

  audio in  : ingest -> v2t.transcribe (Sarvam.ai) -> extract -> embed -> cluster
  transcript: ingest -> v2t._load_transcript        -> extract -> embed -> cluster

The branch is chosen at runtime by looking up ``calls.is_transcript``.

Canonicalization and memory-edge construction are fanned out per touched
cluster inside ``v2t.cluster`` itself, not as a pre-built chain — clusters
aren't known until clustering finishes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from celery import chain
from sqlalchemy import text

from app.core.logging import get_logger
from app.workers.celery_app import celery_app
from app.workers.db import sync_session

log = get_logger("v2t.workers.pipelines")


def _is_transcript(call_id: str) -> bool:
    """Look up whether this call was registered as a pre-labeled transcript."""
    with sync_session() as session:
        row = (
            session.execute(
                text("SELECT is_transcript FROM calls WHERE id = :cid"),
                {"cid": call_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise ValueError(f"call_id {call_id} not found")
    return bool(row["is_transcript"])


def start_call_pipeline(call_id: str | UUID) -> Any:
    """Build and dispatch the Celery chain for a single call.

    Returns the AsyncResult of the head of the chain.
    """
    cid = str(call_id)
    is_transcript = _is_transcript(cid)
    if is_transcript:
        log.info("pipeline_dispatch_transcript", call_id=cid)
        first_stage = celery_app.signature("v2t._load_transcript", args=(cid,))
    else:
        log.info("pipeline_dispatch_audio", call_id=cid)
        first_stage = celery_app.signature("v2t.transcribe", args=(cid,))

    head = chain(
        first_stage,
        celery_app.signature("v2t.extract", args=(cid,)),
        celery_app.signature("v2t.embed", args=(cid,)),
        celery_app.signature("v2t.cluster", args=(cid,)),
    )
    result = head.apply_async()
    # Log WHERE the chain was queued so a stuck pipeline is diagnosable: the head
    # task must land on a queue the worker consumes (default queue = "celery").
    log.info(
        "pipeline_chain_dispatched",
        call_id=cid,
        is_transcript=is_transcript,
        head_task=first_stage.task,
        head_task_id=result.id,
        default_queue=celery_app.conf.task_default_queue,
    )
    return result


__all__ = ["start_call_pipeline"]
