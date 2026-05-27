"""
Integration tests for the FastAPI process basics: /healthz + /metrics.

We intentionally bypass DB/Redis dependencies — these tests verify the
ASGI app boots, exception handlers are wired, and Prometheus is mounted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "env" in body
    assert "version" in body


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    # prometheus_client exposes text/plain with a version parameter.
    assert response.headers["content-type"].startswith("text/plain")
    # The pipeline counters declared in app.core.observability must be exposed.
    assert "v2t_calls_ingested_total" in response.text


def test_request_id_header_round_trips(client: TestClient) -> None:
    response = client.get("/healthz", headers={"x-request-id": "abc-123"})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "abc-123"


def test_cors_allows_localhost_dashboard(client: TestClient) -> None:
    response = client.options(
        "/healthz",
        headers={
            "origin": "http://localhost:3000",
            "access-control-request-method": "GET",
        },
    )
    # Either 200 or 204 depending on Starlette version is acceptable.
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
