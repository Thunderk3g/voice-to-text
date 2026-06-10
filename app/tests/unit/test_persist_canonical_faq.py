"""Unit tests: canonical FAQ persistence backfills the cluster row."""

from __future__ import annotations

from uuid import uuid4

from app.workers.tasks import _persist_canonical_faq


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.statements.append((str(clause), params or {}))


def test_faq_insert_also_backfills_cluster_label() -> None:
    session = _RecordingSession()
    cluster_id = uuid4()
    faq = {
        "cluster_id": cluster_id,
        "canonical_question": "प्रीमियम भुगतान की आवृत्ति कैसे बदलें?",
        "canonical_question_en": "How do I change my premium payment frequency?",
        "suggested_answer": None,
        "language": "hi",
        "confidence": 0.8,
        "version": 1,
    }

    _persist_canonical_faq(session, faq)

    assert len(session.statements) == 2
    insert_sql, _ = session.statements[0]
    update_sql, update_params = session.statements[1]
    assert "insert into canonical_faqs" in insert_sql.lower()
    assert "update semantic_clusters" in update_sql.lower()
    assert update_params["cluster_id"] == str(cluster_id)
    # Label prefers the English canonical form.
    assert update_params["label"] == "How do I change my premium payment frequency?"
    assert update_params["canonical_question"].startswith("प्रीमियम")
    # Version-race guard: only backfill when no newer canonical version exists.
    assert update_params["version"] == 1
    assert "not exists" in update_sql.lower()


def test_label_falls_back_to_original_language() -> None:
    session = _RecordingSession()
    faq = {
        "cluster_id": uuid4(),
        "canonical_question": "How do I file a claim?",
        "canonical_question_en": None,
        "language": "en",
        "confidence": 0.9,
        "version": 1,
    }

    _persist_canonical_faq(session, faq)

    _, update_params = session.statements[1]
    assert update_params["label"] == "How do I file a claim?"


def test_empty_canonical_question_skips_backfill() -> None:
    session = _RecordingSession()
    faq = {
        "cluster_id": uuid4(),
        "canonical_question": "",
        "canonical_question_en": None,
        "language": "en",
        "confidence": 0.0,
        "version": 1,
    }

    _persist_canonical_faq(session, faq)

    assert len(session.statements) == 1  # INSERT only, no cluster UPDATE
