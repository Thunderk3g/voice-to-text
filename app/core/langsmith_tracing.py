"""
Tiny LangSmith trace-id helper.

Wraps a block of work in a LangSmith ``Run`` (when LANGSMITH_TRACING=true) and
yields the run_id so callers can persist it. Failures are logged and the
context manager yields ``None`` — never break the pipeline because tracing is
flaky.

Usage:
    async with traced("extract_call", inputs={"call_id": call_id}) as trace_id:
        ...
        if trace_id:
            store_trace_id(call_id, trace_id)
"""

from __future__ import annotations

import contextlib
import os
import uuid

import structlog

logger = structlog.get_logger(__name__)


@contextlib.contextmanager
def traced(name: str, *, inputs: dict | None = None):
    """Context manager that yields a LangSmith run_id (or None)."""
    if os.environ.get("LANGSMITH_TRACING", "").lower() not in {"1", "true", "yes"}:
        yield None
        return

    try:
        from langsmith import Client  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.debug("langsmith_client_unavailable", error=str(exc))
        yield None
        return

    run_id = str(uuid.uuid4())
    client = Client()
    try:
        client.create_run(
            id=run_id,
            name=name,
            run_type="chain",
            inputs=inputs or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("langsmith_create_run_failed", error=str(exc))
        yield None
        return

    try:
        yield run_id
        try:
            client.update_run(run_id=run_id, end_time=None, outputs={"status": "ok"})
        except Exception as exc:  # noqa: BLE001
            logger.debug("langsmith_update_run_failed", error=str(exc))
    except Exception:
        try:
            client.update_run(run_id=run_id, error="exception in traced block")
        except Exception as exc:  # noqa: BLE001
            logger.debug("langsmith_update_error_failed", error=str(exc))
        raise


__all__ = ["traced"]
