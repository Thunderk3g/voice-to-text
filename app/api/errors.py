"""
Custom API exceptions and shared error response shape.

The platform serializes all errors as `{detail, type}` so the Next.js
dashboard can render them uniformly.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import ORJSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class APIError(Exception):
    """Domain-level API error with HTTP status, detail message and type tag."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_type: str = "api_error",
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_type = error_type


async def api_error_handler(request: Request, exc: APIError) -> ORJSONResponse:
    """Handler for :class:`APIError`."""

    logger.warning(
        "api_error",
        path=request.url.path,
        method=request.method,
        status_code=exc.status_code,
        error_type=exc.error_type,
        detail=exc.detail,
    )
    return ORJSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "type": exc.error_type},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
    """Catch-all so we never leak a stack trace to the client."""

    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
    )
    return ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error.", "type": "internal_error"},
    )
