"""
FastAPI application factory for the v2t API service.

Responsibilities:
  - configure structured logging + OTel tracing on startup
  - mount all routers
  - serve Prometheus `/metrics`
  - install global exception handlers that emit `{detail, type}`
  - dispose the SQLAlchemy engine on shutdown
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.errors import (
    APIError,
    api_error_handler,
    unhandled_exception_handler,
)
from app.api.routes import (
    admin,
    analytics,
    calls,
    clusters,
    faq,
    feedback,
    health,
    ingest,
    knowledge_graph,
    memory_graph,
    search,
)
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_tracing


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Attach a request id + log structured access entries."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        logger = get_logger("v2t.api.request")
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception("request_failed", duration_ms=round(duration_ms, 2))
            structlog.contextvars.clear_contextvars()
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        structlog.contextvars.clear_contextvars()
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Middleware (incl. OTel) must already be wired before the app starts —
    # Starlette no longer permits add_middleware after startup. Tracing/OTel
    # init runs at app construction time in create_app() instead.
    logger = get_logger("v2t.api.lifespan")
    logger.info("api_starting", version=__version__, env=get_settings().app_env)
    try:
        yield
    finally:
        logger.info("api_stopping")
        try:
            from app.db.session import engine  # type: ignore

            await engine.dispose()
            logger.info("db_engine_disposed")
        except Exception as exc:  # noqa: BLE001
            logger.warning("db_engine_dispose_failed", error=str(exc))


def create_app() -> FastAPI:
    """Build the FastAPI app. Called by `app = create_app()` and tests."""

    settings = get_settings()
    configure_logging()
    init_tracing("v2t-api")

    app = FastAPI(
        title="v2t — insurance call intelligence",
        version=__version__,
        default_response_class=ORJSONResponse,
        lifespan=_lifespan,
    )

    # OTel instrumentation must be installed before the app starts so its
    # middleware can be added.
    try:
        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        pass

    # ---- Middleware ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # ---- Exception handlers ----
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ---- Routers ----
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(calls.router)
    app.include_router(search.router)
    app.include_router(clusters.router)
    app.include_router(faq.router)
    app.include_router(memory_graph.router)
    app.include_router(knowledge_graph.router)
    app.include_router(analytics.router)
    app.include_router(feedback.router)
    app.include_router(admin.router)

    # ---- Prometheus ----
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # Suppress noisy app_env reference warning at import time.
    _ = settings
    return app


app = create_app()
