"""GET /knowledge-graph — serves the typed entity graph as Cytoscape-style JSON.

The graph provider dependency is overridden with an in-memory fixture graph so
the endpoint is exercised without a DB.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routes.knowledge_graph import get_knowledge_graph
from app.services.knowledge_graph import build_call_graph


def _fixture_graph():
    lead = {
        "full_name": "Asha Rao", "phone": "9876543210", "email": None, "age": None,
        "gender": None, "occupation": None, "education": None, "income_band": None,
        "pincode": None, "product_interest": None, "policy_no": None,
        "callback_time": None, "grounded_fields": ["phone"],
    }
    analysis = {
        "lead": lead, "disposition": "resolved", "disposition_confidence": 0.8,
        "disposition_rationale": "answered query", "sentiment": "positive",
        "sentiment_confidence": 0.7, "escalation": False, "model": "m",
    }
    return build_call_graph(analysis, call_id="25689211", call_date="2026-01-05")


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_knowledge_graph] = _fixture_graph
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_knowledge_graph_serializes_typed_graph(client: TestClient) -> None:
    r = client.get("/knowledge-graph")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body

    types = {n["type"] for n in body["nodes"]}
    assert {"call", "disposition", "sentiment", "lead"} <= types

    # edges are Cytoscape-style (source/target/relation/weight)
    assert body["edges"], body
    for e in body["edges"]:
        assert {"source", "target", "relation", "weight"} <= set(e)
    rels = {e["relation"] for e in body["edges"]}
    assert "has_disposition" in rels
    assert "received_call" in rels
