"""
POST /feedback — persist a FeedbackAnnotation and dispatch the matching
remediation task.

Action → task name mapping:
  - MERGE_CLUSTERS   -> v2t.feedback.merge
  - SPLIT_CLUSTER    -> v2t.feedback.split
  - RELABEL_INTENT   -> v2t.feedback.relabel
  - REASSIGN_QUESTION-> v2t.feedback.reassign
  - REGENERATE_FAQ   -> v2t.canonicalize
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.celery_sender import send
from app.api.dependencies import get_db
from app.api.errors import APIError
from app.core.logging import get_logger
from app.models.enums import FeedbackAction
from app.models.schemas import FeedbackAnnotation

logger = get_logger(__name__)
router = APIRouter(tags=["feedback"])

_ACTION_TASKS: dict[FeedbackAction, str] = {
    FeedbackAction.MERGE_CLUSTERS: "v2t.feedback.merge",
    FeedbackAction.SPLIT_CLUSTER: "v2t.feedback.split",
    FeedbackAction.RELABEL_INTENT: "v2t.feedback.relabel",
    FeedbackAction.REASSIGN_QUESTION: "v2t.feedback.reassign",
    FeedbackAction.REGENERATE_FAQ: "v2t.canonicalize",
}


@router.post("/feedback", status_code=status.HTTP_202_ACCEPTED)
async def submit_feedback(
    payload: FeedbackAnnotation,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    from app.db.models import FeedbackAnnotationORM

    task_name = _ACTION_TASKS.get(payload.action)
    if task_name is None:
        raise APIError(
            f"Unsupported feedback action: {payload.action}",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_type="unsupported_feedback_action",
        )

    row = FeedbackAnnotationORM(
        action=payload.action,
        payload=payload.payload,
        author=payload.author,
        note=payload.note,
    )
    try:
        db.add(row)
        await db.flush()
        annotation_id = str(row.id)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("feedback_persist_failed", error=str(exc))
        raise APIError(
            "Failed to persist feedback annotation.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="feedback_persist_failed",
        ) from exc

    # The remediation tasks consume the feedback PAYLOAD, not the annotation
    # row id. merge/split/relabel/reassign take the payload dict (they read
    # payload["source_id"], payload["cluster_id"], ...); canonicalize
    # (REGENERATE_FAQ) takes a bare cluster_id string. Passing annotation_id
    # here made every task raise "'str' object has no attribute 'get'".
    if payload.action == FeedbackAction.REGENERATE_FAQ:
        task_arg: object = (payload.payload or {}).get("cluster_id")
    else:
        task_arg = payload.payload

    try:
        task_id = send(task_name, task_arg)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "feedback_dispatch_failed",
            annotation_id=annotation_id,
            task=task_name,
            error=str(exc),
        )
        raise APIError(
            "Feedback stored but task dispatch failed.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_type="feedback_dispatch_failed",
        ) from exc

    logger.info(
        "feedback_received",
        annotation_id=annotation_id,
        action=str(payload.action),
        task=task_name,
        task_id=task_id,
    )
    return {"annotation_id": annotation_id, "task": task_name, "task_id": task_id}
