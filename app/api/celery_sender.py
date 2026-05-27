"""
Thin Celery client used by the FastAPI process to enqueue background work.

The API container does NOT import worker task code — it only dispatches
by registered task name. This keeps the API image free of GPU / ML deps
and avoids import-order coupling with `app.workers.tasks`.
"""

from __future__ import annotations

from typing import Any

from celery import Celery

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

celery_sender: Celery = Celery(
    "v2t-api-sender",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
# We never run tasks in this process; ensure that's enforced.
celery_sender.conf.update(
    task_always_eager=False,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
)


def send(name: str, *args: Any, queue: str | None = None, **kwargs: Any) -> str:
    """Dispatch a task by registered name and return its task id."""

    options: dict[str, Any] = {}
    if queue:
        options["queue"] = queue
    async_result = celery_sender.send_task(name, args=list(args), kwargs=kwargs, **options)
    logger.info("celery_task_dispatched", task=name, task_id=async_result.id, queue=queue)
    return async_result.id
