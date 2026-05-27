"""
Celery application factory + global configuration for the v2t worker fleet.

The application is intentionally minimal:
  * broker / backend come from `Settings`
  * GPU-bound stages (embedding only — STT/diarization happen upstream
    in Servo AI) are routed to `gpu.heavy`
  * everything else falls back to the default queue
  * `task_acks_late=True` + `worker_prefetch_multiplier=1` give us at-least-once
    semantics so retries don't drop messages while a worker is being shut down
  * structured logging and OTel tracing are wired on worker start

Beat schedule:
  * `v2t.batch_recluster` runs daily at 02:00 UTC and is the canonical entry
    point for periodic re-clustering of the embeddings table.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_tracing

_settings = get_settings()

celery_app = Celery(
    "v2t",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=["app.workers.tasks"],
)


def _route_task(name: str, args, kwargs, options, task=None, **kw):  # noqa: ANN001
    """Dynamic router — keeps queue topology declarative in one place.

    Any task whose name contains one of the GPU-bound stage markers gets
    pushed onto the `gpu.heavy` queue. Everything else stays on the default
    queue so CPU workers can pick them up.
    """
    if not name:
        return None
    if ".embed" in name:
        return {"queue": "gpu.heavy"}
    return None


celery_app.conf.update(
    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_reject_on_worker_lost=True,
    # Retry defaults
    task_default_retry_delay=10,
    task_max_retries=5,
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Routing
    task_default_queue="default",
    task_routes=(_route_task,),
    # Beat
    beat_schedule={
        "v2t.batch_recluster.daily": {
            "task": "v2t.batch_recluster",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)


@worker_process_init.connect
def _on_worker_init(**_: object) -> None:
    """Configure logging, tracing, and Celery instrumentation per process."""
    configure_logging()
    init_tracing("v2t-worker")
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover - tracing is optional
        get_logger("v2t.worker").warning(
            "celery_instrumentation_failed", error=str(exc)
        )


__all__ = ["celery_app"]
