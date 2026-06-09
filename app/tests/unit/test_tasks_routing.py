"""
Unit tests: Celery routing rules.

The router lives in `app.workers.celery_app` and partitions tasks between
the default queue and `gpu.heavy` based on the task name. These tests
exercise the router function directly so they don't need a broker.
"""

from __future__ import annotations

import pytest

from app.workers import celery_app as celery_module

# Import the tasks module for its decorator side effects: ``@celery_app.task``
# only registers a task when the module is imported. At runtime the worker does
# this via ``include=["app.workers.tasks"]`` at startup; in-process (here) we
# must import it explicitly, otherwise ``test_known_tasks_registered`` sees an
# empty registry.
import app.workers.tasks  # noqa: E402,F401
from app.workers.celery_app import celery_app  # noqa: E402


GPU_TASKS = ["v2t.embed"]
CPU_TASKS = [
    "v2t.ingest",
    "v2t._load_transcript",
    "v2t.transcribe",
    "v2t.extract",
    "v2t.cluster",
    "v2t.canonicalize",
    "v2t.memory_edges",
    "v2t.batch_recluster",
    "v2t.feedback.merge",
    "v2t.feedback.split",
    "v2t.feedback.relabel",
    "v2t.feedback.reassign",
]


@pytest.mark.parametrize("name", GPU_TASKS)
def test_gpu_tasks_route_to_gpu_heavy(name: str) -> None:
    route = celery_module._route_task(name, (), {}, {})
    assert route is not None
    assert route["queue"] == "gpu.heavy"


@pytest.mark.parametrize("name", CPU_TASKS)
def test_cpu_tasks_use_default_queue(name: str) -> None:
    route = celery_module._route_task(name, (), {}, {})
    # Router returns None -> Celery applies task_default_queue ("default").
    assert route is None


def test_celery_app_basic_config() -> None:
    """Sanity check the Celery app constants we rely on."""
    assert celery_app.main == "v2t"
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_track_started is True
    assert conf.task_default_retry_delay == 10
    assert conf.task_max_retries == 5
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert "json" in conf.accept_content


def test_beat_schedule_includes_batch_recluster() -> None:
    schedule = celery_app.conf.beat_schedule
    assert "v2t.batch_recluster.daily" in schedule
    entry = schedule["v2t.batch_recluster.daily"]
    assert entry["task"] == "v2t.batch_recluster"
    cron = entry["schedule"]
    # Daily at 02:00
    assert getattr(cron, "hour", None) == {2} or 2 in getattr(cron, "hour", set())
    assert getattr(cron, "minute", None) == {0} or 0 in getattr(cron, "minute", set())


def test_known_tasks_registered() -> None:
    """Every contract task name must be registered on the app."""
    expected = set(GPU_TASKS + CPU_TASKS)
    registered = set(celery_app.tasks.keys())
    missing = expected - registered
    assert not missing, f"Tasks not registered: {missing}"
