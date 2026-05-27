"""
Integration test: happy-path pipeline chain with `task_always_eager=True`.

This exercises the orchestration logic in `app.workers.tasks` and
`app.workers.pipelines` end-to-end while *mocking out every external
service* (transcript loader, LLM extractor, embeddings, ClusterEngine,
canonicalization, memory-graph builder).

Note: STT and speaker diarization are handled UPSTREAM by Servo AI, so
this test does NOT mock Whisper or pyannote.

The test is marked `requires_postgres`: it expects a Postgres reachable
via `Settings.database_url_sync`. If the DSN is unreachable, it is
skipped — this keeps CI green on machines without a DB while still
giving us a real exercise locally and in the integration ring.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.models.enums import CallStatus
from app.models.schemas import (
    EmbeddingRecord,
    ExtractedQuestion,
    ExtractionResult,
    UtteranceSchema,
)


pytestmark = pytest.mark.requires_postgres


def _postgres_reachable() -> bool:
    try:
        engine = create_engine(
            get_settings().database_url_sync, pool_pre_ping=True
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except OperationalError:
        return False
    except Exception:
        return False


if not _postgres_reachable():
    pytest.skip(
        "Postgres DSN unreachable; skipping pipeline chain integration test",
        allow_module_level=True,
    )


# ----------------------------------------------------------------------------
# Eager-mode Celery configuration
# ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _eager_celery():
    from app.workers.celery_app import celery_app

    prev = {
        "task_always_eager": celery_app.conf.task_always_eager,
        "task_eager_propagates": celery_app.conf.task_eager_propagates,
    }
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        for k, v in prev.items():
            setattr(celery_app.conf, k, v)


# ----------------------------------------------------------------------------
# Service mocks
# ----------------------------------------------------------------------------
@pytest.fixture
def call_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def cluster_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def seed_call(call_id: str):
    """Insert a minimal calls row and clean up afterwards."""
    from app.workers.db import sync_session

    with sync_session() as session:
        session.execute(
            text(
                """
                INSERT INTO calls (id, source_uri, is_transcript, status, created_at, updated_at)
                VALUES (:id, :uri, FALSE, :s, NOW(), NOW())
                """
            ),
            {"id": call_id, "uri": "minio://audio/example.wav", "s": CallStatus.PENDING.value},
        )
    yield call_id
    with sync_session() as session:
        session.execute(text("DELETE FROM cluster_members WHERE 1=1"))
        session.execute(text("DELETE FROM embeddings WHERE 1=1"))
        session.execute(text("DELETE FROM extracted_questions WHERE call_id = :c"), {"c": call_id})
        session.execute(text("DELETE FROM utterances WHERE call_id = :c"), {"c": call_id})
        session.execute(text("DELETE FROM calls WHERE id = :c"), {"c": call_id})


@pytest.fixture
def patch_services(monkeypatch, call_id: str, cluster_id: str):
    """Patch every external service so only orchestration is exercised."""

    qid = uuid.uuid4()

    async def _extract(self, cid, utterances):
        return ExtractionResult(
            call_id=cid,
            questions=[
                ExtractedQuestion(
                    id=qid,
                    call_id=cid,
                    raw_text="hello",
                    normalized_text="hello",
                    language="en",
                )
            ],
            used_model="mock",
        )

    async def _embed_questions(self, questions):
        return [
            EmbeddingRecord(
                question_id=qid,
                model="mock-e5",
                dim=4,
                vector=[0.1, 0.2, 0.3, 0.4],
            )
        ]

    async def _assign_incremental(self, embeddings):
        from app.models.schemas import ClusterMember
        from datetime import datetime, timezone

        return [
            ClusterMember(
                cluster_id=uuid.UUID(cluster_id),
                question_id=qid,
                similarity=0.91,
                assigned_at=datetime.now(timezone.utc),
            )
        ]

    async def _rebatch_all(self):
        return {"created": 0, "updated": 0, "dissolved": 0}

    # Seed a cluster row so persist_assignments / fan-out tasks have something
    # to reference.
    from app.workers.db import sync_session

    with sync_session() as session:
        session.execute(
            text(
                """
                INSERT INTO semantic_clusters
                    (id, label, canonical_question, centroid, dominant_language,
                     dominant_intents, frequency, last_updated, is_stable)
                VALUES (:id, 'mock', 'hello?', '[0.1,0.2,0.3,0.4]', 'en',
                        ARRAY[]::text[], 0, NOW(), TRUE)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": cluster_id},
        )

    # Patch the service classes used inside tasks.
    import app.workers.transcript_pipeline as tx_mod
    import app.services.extraction.llm_extractor as extract_mod
    import app.services.embedding.e5 as embed_mod
    import app.clustering.engine as clu_mod
    import app.services.canonicalization.faq as faq_mod
    import app.services.memory_graph.builder as mem_mod

    async def _noop_transcript_loader(_call_id):
        return None

    monkeypatch.setattr(
        tx_mod, "load_transcript_to_utterances", _noop_transcript_loader, raising=False
    )
    monkeypatch.setattr(extract_mod.LLMExtractor, "extract", _extract, raising=False)
    monkeypatch.setattr(embed_mod.EmbeddingService, "embed_questions", _embed_questions, raising=False)
    monkeypatch.setattr(clu_mod.ClusterEngine, "assign_incremental", _assign_incremental, raising=False)
    monkeypatch.setattr(clu_mod.ClusterEngine, "rebatch_all", _rebatch_all, raising=False)

    async def _canon(self, cid):
        from datetime import datetime, timezone
        from app.models.schemas import CanonicalFAQ

        return CanonicalFAQ(
            id=uuid.uuid4(),
            cluster_id=cid,
            canonical_question="hello?",
            language="en",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    async def _build_edges(self, cid):
        return []

    monkeypatch.setattr(faq_mod.FAQCanonicalizer, "canonicalize", _canon, raising=False)
    monkeypatch.setattr(mem_mod.MemoryGraphBuilder, "build_edges_for", _build_edges, raising=False)

    yield {"cluster_id": cluster_id, "question_id": qid}

    with sync_session() as session:
        session.execute(text("DELETE FROM memory_edges WHERE source_cluster_id = :c"), {"c": cluster_id})
        session.execute(text("DELETE FROM canonical_faqs WHERE cluster_id = :c"), {"c": cluster_id})
        session.execute(text("DELETE FROM semantic_clusters WHERE id = :c"), {"c": cluster_id})


# ----------------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------------
def test_pipeline_chain_happy_path(seed_call, patch_services):
    """Drive the whole pipeline end-to-end in eager mode."""
    from app.workers.db import sync_session
    from app.workers.tasks import ingest_call

    call_id = seed_call
    ingest_call.apply(args=(call_id,)).get(disable_sync_subtasks=False)

    with sync_session() as session:
        status = session.execute(
            text("SELECT status FROM calls WHERE id = :c"),
            {"c": call_id},
        ).scalar_one()
        # Final stage status — pipeline must have walked all the way to CLUSTERED.
        assert status == CallStatus.CLUSTERED.value

        # Cluster row should exist (it was seeded) and have at least one member.
        member_count = session.execute(
            text(
                "SELECT COUNT(*) FROM cluster_members WHERE cluster_id = :c"
            ),
            {"c": patch_services["cluster_id"]},
        ).scalar_one()
        assert member_count >= 1
