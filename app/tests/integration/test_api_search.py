"""
Integration test for POST /search.

We monkeypatch:
  - the embedding service singleton, so no GPU/model is required
  - the AsyncSession dependency to return a stub that yields fake rows

The assertion is purely on the SearchResponse shape so the test is
independent of pgvector wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies as deps
from app.api.main import create_app
from app.models.enums import Intent, Language, QuestionType


class _StubExtractedQuestion:
    """Duck-typed stand-in for the ORM row used by `from_attributes`."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.call_id = uuid4()
        self.utterance_id = uuid4()
        self.raw_text = "Mera premium kab due hai?"
        self.normalized_text = "When is my premium due?"
        self.english_gloss = "When is my premium due?"
        self.question_type = QuestionType.QUESTION
        self.intent = Intent.PREMIUM_PAYMENT
        self.secondary_intents = []
        self.language = Language.HINGLISH
        self.confidence = 0.92
        self.extracted_at = datetime.now(timezone.utc)


class _StubEmbeddingService:
    async def embed(self, texts: list[str], *, role: str = "passage") -> list[list[float]]:
        assert role == "query"
        return [[0.01] * 1024 for _ in texts]


class _StubResult:
    def __init__(self, rows: list[tuple[Any, Any, float]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, Any, float]]:
        return list(self._rows)


class _StubSession:
    """Minimal AsyncSession surface used by the /search route."""

    def __init__(self) -> None:
        cluster_id = uuid4()
        self._rows = [
            (_StubExtractedQuestion(), cluster_id, 0.91),
            (_StubExtractedQuestion(), cluster_id, 0.83),
            (_StubExtractedQuestion(), None, 0.71),
        ]

    async def execute(self, _stmt: Any) -> _StubResult:  # noqa: D401
        return _StubResult(self._rows)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    stub_embed = _StubEmbeddingService()
    monkeypatch.setattr(deps, "get_embedding_service", lambda: stub_embed)

    async def _override_get_db():
        yield _StubSession()

    app = create_app()
    app.dependency_overrides[deps.get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_search_returns_expected_shape(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={"query": "premium due date", "top_k": 5, "min_score": 0.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["query"] == "premium due date"
    assert isinstance(body["hits"], list)
    assert len(body["hits"]) == 3
    for hit in body["hits"]:
        assert "question" in hit
        assert "cluster_id" in hit
        assert "score" in hit
        q = hit["question"]
        assert "raw_text" in q
        assert "normalized_text" in q
        assert q["language"] in {lang.value for lang in Language}
        assert q["intent"] in {intent.value for intent in Intent}
        assert isinstance(hit["score"], (int, float))

    aggregates = body["cluster_aggregates"]
    assert isinstance(aggregates, list)
    # Two of three rows share a cluster_id, so one aggregate entry is expected.
    assert len(aggregates) == 1
    agg = aggregates[0]
    assert agg["hit_count"] == 2
    assert agg["top_intent"] == Intent.PREMIUM_PAYMENT.value
    assert agg["max_score"] >= agg["avg_score"]


def test_search_rejects_empty_query(client: TestClient) -> None:
    response = client.post("/search", json={"query": "   "})
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "empty_query"
    assert "detail" in body
