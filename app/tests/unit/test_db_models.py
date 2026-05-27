"""Smoke tests for the SQLAlchemy ORM models.

These tests do not touch a database — they instantiate each model in-memory,
assert UUID PK + enum column wiring, and confirm pgvector columns have the
declared dimension. They protect against accidental schema regressions.
"""

from __future__ import annotations

import uuid

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Enum as SAEnum

from app.db import models as m
from app.db.base import Base
from app.models.enums import (
    CallStatus,
    EdgeRelation,
    FeedbackAction,
    Intent,
    Language,
    QuestionType,
    Speaker,
)


# ---------------------------------------------------------------------------
# Module import / Base metadata
# ---------------------------------------------------------------------------
def test_module_imports_all_models() -> None:
    expected = {
        "Call",
        "Utterance",
        "ExtractedQuestionORM",
        "Embedding",
        "SemanticCluster",
        "ClusterMemberORM",
        "CanonicalFAQORM",
        "MemoryEdgeORM",
        "FeedbackAnnotationORM",
    }
    for name in expected:
        assert hasattr(m, name), f"missing model: {name}"


def test_metadata_contains_all_nine_tables() -> None:
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "calls",
        "utterances",
        "extracted_questions",
        "embeddings",
        "semantic_clusters",
        "cluster_members",
        "canonical_faqs",
        "memory_edges",
        "feedback_annotations",
    }
    missing = expected - table_names
    assert not missing, f"missing tables in metadata: {missing}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _col_type(model_cls, col_name):
    return Base.metadata.tables[model_cls.__tablename__].c[col_name].type


# ---------------------------------------------------------------------------
# UUID primary key smoke
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "model_cls, pk_col",
    [
        (m.Call, "id"),
        (m.Utterance, "id"),
        (m.ExtractedQuestionORM, "id"),
        (m.Embedding, "id"),
        (m.SemanticCluster, "id"),
        (m.CanonicalFAQORM, "id"),
        (m.MemoryEdgeORM, "id"),
        (m.FeedbackAnnotationORM, "id"),
        (m.ClusterMemberORM, "question_id"),
    ],
)
def test_primary_key_is_uuid(model_cls, pk_col) -> None:
    table = Base.metadata.tables[model_cls.__tablename__]
    pk_cols = list(table.primary_key.columns)
    assert pk_col in {c.name for c in pk_cols}


# ---------------------------------------------------------------------------
# Vector dim
# ---------------------------------------------------------------------------
def test_embedding_vector_has_dim_1024() -> None:
    t = _col_type(m.Embedding, "vector")
    assert isinstance(t, Vector)
    assert t.dim == 1024


def test_semantic_cluster_centroid_has_dim_1024() -> None:
    t = _col_type(m.SemanticCluster, "centroid")
    assert isinstance(t, Vector)
    assert t.dim == 1024


def test_embedding_constant_matches() -> None:
    assert m.EMBEDDING_DIM == 1024


# ---------------------------------------------------------------------------
# Enum columns
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "model_cls, col, py_enum",
    [
        (m.Call, "status", CallStatus),
        (m.Call, "detected_language", Language),
        (m.Utterance, "speaker", Speaker),
        (m.Utterance, "language", Language),
        (m.ExtractedQuestionORM, "question_type", QuestionType),
        (m.ExtractedQuestionORM, "intent", Intent),
        (m.ExtractedQuestionORM, "language", Language),
        (m.SemanticCluster, "dominant_language", Language),
        (m.CanonicalFAQORM, "language", Language),
        (m.MemoryEdgeORM, "relation", EdgeRelation),
        (m.FeedbackAnnotationORM, "action", FeedbackAction),
    ],
)
def test_enum_column_wired_to_python_enum(model_cls, col, py_enum) -> None:
    t = _col_type(model_cls, col)
    assert isinstance(t, SAEnum)
    assert t.enum_class is py_enum
    # All Python enum values must be present in the PG enum.
    pg_values = set(t.enums)
    py_values = {member.value for member in py_enum}
    assert py_values.issubset(pg_values), (py_values - pg_values)


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------
def test_instantiate_all_models_in_memory() -> None:
    call = m.Call(
        id=uuid.uuid4(),
        source_uri="minio://audio/abc.wav",
        is_transcript=False,
        status=CallStatus.PENDING,
        call_metadata={"agent_id": "A1"},
    )
    assert isinstance(call.id, uuid.UUID)
    assert call.status is CallStatus.PENDING

    utt = m.Utterance(
        id=uuid.uuid4(),
        call_id=call.id,
        speaker=Speaker.CUSTOMER,
        start_ts=0.0,
        end_ts=1.5,
        text="hello",
        language=Language.ENGLISH,
        confidence=0.9,
    )
    assert utt.speaker is Speaker.CUSTOMER

    q = m.ExtractedQuestionORM(
        id=uuid.uuid4(),
        call_id=call.id,
        utterance_id=utt.id,
        raw_text="kya hai",
        normalized_text="kya hai",
        english_gloss="what is it",
        question_type=QuestionType.QUESTION,
        intent=Intent.POLICY_DETAILS,
        secondary_intents=[Intent.RENEWAL.value],
        language=Language.HINGLISH,
        confidence=0.8,
    )
    assert q.intent is Intent.POLICY_DETAILS

    vec = [0.0] * 1024
    emb = m.Embedding(
        id=uuid.uuid4(),
        question_id=q.id,
        model="intfloat/multilingual-e5-large",
        dim=1024,
        vector=vec,
    )
    assert emb.dim == 1024
    assert len(emb.vector) == 1024

    cluster = m.SemanticCluster(
        id=uuid.uuid4(),
        label="renewal",
        canonical_question="How do I renew?",
        centroid=vec,
        dominant_language=Language.ENGLISH,
        dominant_intents=[Intent.RENEWAL.value],
        frequency=3,
        is_stable=True,
    )
    assert cluster.dominant_language is Language.ENGLISH

    member = m.ClusterMemberORM(
        cluster_id=cluster.id,
        question_id=q.id,
        similarity=0.92,
    )
    assert member.cluster_id == cluster.id

    faq = m.CanonicalFAQORM(
        id=uuid.uuid4(),
        cluster_id=cluster.id,
        canonical_question="How do I renew my policy?",
        canonical_question_en="How do I renew my policy?",
        suggested_answer="Log into the portal...",
        language=Language.ENGLISH,
        confidence=0.95,
        version=1,
    )
    assert faq.version == 1

    edge = m.MemoryEdgeORM(
        id=uuid.uuid4(),
        source_cluster_id=cluster.id,
        target_cluster_id=uuid.uuid4(),
        relation=EdgeRelation.LEADS_TO,
        weight=0.7,
        reason="follow-up",
    )
    assert edge.relation is EdgeRelation.LEADS_TO

    fb = m.FeedbackAnnotationORM(
        id=uuid.uuid4(),
        action=FeedbackAction.MERGE_CLUSTERS,
        payload={"a": str(cluster.id)},
        author="qa-1",
        note="duplicates",
    )
    assert fb.action is FeedbackAction.MERGE_CLUSTERS


# ---------------------------------------------------------------------------
# Memory edges unique constraint
# ---------------------------------------------------------------------------
def test_memory_edges_unique_constraint() -> None:
    table = Base.metadata.tables["memory_edges"]
    uqs = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert any(
        {col.name for col in uq.columns}
        == {"source_cluster_id", "target_cluster_id", "relation"}
        for uq in uqs
    ), "memory_edges must have unique(source, target, relation)"
