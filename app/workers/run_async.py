"""
Bridge to invoke async services from sync Celery tasks.

Each call creates a fresh event loop and tears it down cleanly. This avoids
the classic "Task was destroyed but it is pending" warning and prevents
loop reuse across tasks (which would be unsafe when Celery prefetches more
than one task per process or when a previous loop was closed by error).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T:
    """Run an awaitable on a brand-new event loop and return its result.

    The loop is always closed, even on exception, and async generators are
    shut down so background tasks (e.g. HTTPX pools) get a chance to clean
    up before the loop disappears.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result: Any = loop.run_until_complete(coro)
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        return result
    finally:
        try:
            asyncio.set_event_loop(None)
        finally:
            loop.close()


__all__ = ["run_async"]
