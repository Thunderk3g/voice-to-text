"""initial schema with pgvector

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000

Creates all 9 tables, named PG enum types, the pgvector extension, ivfflat
vector indexes on `embeddings.vector` and `semantic_clusters.centroid`, btree
indexes on common filter columns, and the unique constraint on memory_edges.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirror enum value lists from app.models.enums (kept inline so migrations are
# self-contained and replayable on a frozen DB image).
SPEAKER_VALUES = ("AGENT", "CUSTOMER", "UNKNOWN")
LANGUAGE_VALUES = ("hi", "en", "hi-en", "hi-roman", "ta", "te", "other")
INTENT_VALUES = (
    "policy_details",
    "premium_payment",
    "claim_process",
    "claim_rejection",
    "renewal",
    "nominee_update",
    "document_request",
    "cancellation",
    "maturity_benefit",
    "health_coverage",
    "exclusions",
    "agent_complaint",
    "grievance",
    "other_insurance",
    "other",
)
CALL_STATUS_VALUES = (
    "pending",
    "stt_running",
    "stt_done",
    "diarization_running",
    "diarization_done",
    "extraction_running",
    "extraction_done",
    "embedding_done",
    "clustered",
    "failed",
)
QUESTION_TYPE_VALUES = ("question", "complaint", "doubt", "intent")
FEEDBACK_ACTION_VALUES = (
    "merge_clusters",
    "split_cluster",
    "relabel_intent",
    "regenerate_faq",
    "reassign_question",
)
EDGE_RELATION_VALUES = (
    "leads_to",
    "related_to",
    "subset_of",
    "opposes",
    "caused_by",
    "co_occurs",
)


def upgrade() -> None:
    # -- Extensions --------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # for gen_random_uuid()

    # -- PG enum types -----------------------------------------------------
    speaker_enum = postgresql.ENUM(*SPEAKER_VALUES, name="speaker", create_type=False)
    language_enum = postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False)
    intent_enum = postgresql.ENUM(*INTENT_VALUES, name="intent", create_type=False)
    call_status_enum = postgresql.ENUM(
        *CALL_STATUS_VALUES, name="call_status", create_type=False
    )
    question_type_enum = postgresql.ENUM(
        *QUESTION_TYPE_VALUES, name="question_type", create_type=False
    )
    feedback_action_enum = postgresql.ENUM(
        *FEEDBACK_ACTION_VALUES, name="feedback_action", create_type=False
    )
    edge_relation_enum = postgresql.ENUM(
        *EDGE_RELATION_VALUES, name="edge_relation", create_type=False
    )
    bind = op.get_bind()
    speaker_enum.create(bind, checkfirst=True)
    language_enum.create(bind, checkfirst=True)
    intent_enum.create(bind, checkfirst=True)
    call_status_enum.create(bind, checkfirst=True)
    question_type_enum.create(bind, checkfirst=True)
    feedback_action_enum.create(bind, checkfirst=True)
    edge_relation_enum.create(bind, checkfirst=True)

    # -- calls -------------------------------------------------------------
    op.create_table(
        "calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("is_transcript", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "status",
            postgresql.ENUM(*CALL_STATUS_VALUES, name="call_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::call_status"),
        ),
        sa.Column(
            "detected_language",
            postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False),
            nullable=True,
        ),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_calls_status", "calls", ["status"])
    op.create_index("ix_calls_created_at", "calls", ["created_at"])
    op.create_index("ix_calls_detected_language", "calls", ["detected_language"])

    # -- utterances --------------------------------------------------------
    op.create_table(
        "utterances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "speaker",
            postgresql.ENUM(*SPEAKER_VALUES, name="speaker", create_type=False),
            nullable=False,
            server_default=sa.text("'UNKNOWN'::speaker"),
        ),
        sa.Column("start_ts", sa.Float(), nullable=False),
        sa.Column("end_ts", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "language",
            postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("words", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_utterances_call_id", "utterances", ["call_id"])
    op.create_index("ix_utterances_language", "utterances", ["language"])
    op.create_index("ix_utterances_speaker", "utterances", ["speaker"])

    # -- extracted_questions -----------------------------------------------
    op.create_table(
        "extracted_questions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "utterance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("utterances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("english_gloss", sa.Text(), nullable=True),
        sa.Column(
            "question_type",
            postgresql.ENUM(
                *QUESTION_TYPE_VALUES, name="question_type", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'question'::question_type"),
        ),
        sa.Column(
            "intent",
            postgresql.ENUM(*INTENT_VALUES, name="intent", create_type=False),
            nullable=False,
            server_default=sa.text("'other'::intent"),
        ),
        sa.Column(
            "secondary_intents",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "language",
            postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_extracted_questions_call_id", "extracted_questions", ["call_id"])
    op.create_index(
        "ix_extracted_questions_utterance_id", "extracted_questions", ["utterance_id"]
    )
    op.create_index("ix_extracted_questions_intent", "extracted_questions", ["intent"])
    op.create_index("ix_extracted_questions_language", "extracted_questions", ["language"])
    op.create_index(
        "ix_extracted_questions_extracted_at", "extracted_questions", ["extracted_at"]
    )

    # -- embeddings --------------------------------------------------------
    op.create_table(
        "embeddings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extracted_questions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False, server_default=sa.text("1024")),
        sa.Column("vector", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # ivfflat cosine index on embeddings.vector
    op.execute(
        "CREATE INDEX ix_embeddings_vector_cosine ON embeddings "
        "USING ivfflat (vector vector_cosine_ops) WITH (lists = 100);"
    )

    # -- semantic_clusters -------------------------------------------------
    op.create_table(
        "semantic_clusters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("canonical_question", sa.Text(), nullable=True),
        sa.Column("centroid", Vector(1024), nullable=False),
        sa.Column(
            "dominant_language",
            postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "dominant_intents",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("frequency", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("is_stable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_semantic_clusters_frequency", "semantic_clusters", ["frequency"])
    op.create_index("ix_semantic_clusters_is_stable", "semantic_clusters", ["is_stable"])
    op.create_index(
        "ix_semantic_clusters_dominant_language",
        "semantic_clusters",
        ["dominant_language"],
    )
    op.execute(
        "CREATE INDEX ix_semantic_clusters_centroid_cosine ON semantic_clusters "
        "USING ivfflat (centroid vector_cosine_ops) WITH (lists = 100);"
    )

    # -- cluster_members ---------------------------------------------------
    op.create_table(
        "cluster_members",
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extracted_questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("similarity", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_cluster_members_cluster_id", "cluster_members", ["cluster_id"])
    op.create_index("ix_cluster_members_assigned_at", "cluster_members", ["assigned_at"])

    # -- canonical_faqs ----------------------------------------------------
    op.create_table(
        "canonical_faqs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_question", sa.Text(), nullable=False),
        sa.Column("canonical_question_en", sa.Text(), nullable=True),
        sa.Column("suggested_answer", sa.Text(), nullable=True),
        sa.Column(
            "language",
            postgresql.ENUM(*LANGUAGE_VALUES, name="language", create_type=False),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_canonical_faqs_cluster_id", "canonical_faqs", ["cluster_id"])
    op.create_index("ix_canonical_faqs_language", "canonical_faqs", ["language"])
    op.create_index("ix_canonical_faqs_updated_at", "canonical_faqs", ["updated_at"])

    # -- memory_edges ------------------------------------------------------
    op.create_table(
        "memory_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation",
            postgresql.ENUM(
                *EDGE_RELATION_VALUES, name="edge_relation", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "source_cluster_id",
            "target_cluster_id",
            "relation",
            name="uq_memory_edges_source_target_relation",
        ),
    )
    op.create_index("ix_memory_edges_source_cluster_id", "memory_edges", ["source_cluster_id"])
    op.create_index("ix_memory_edges_target_cluster_id", "memory_edges", ["target_cluster_id"])
    op.create_index("ix_memory_edges_weight", "memory_edges", ["weight"])

    # -- feedback_annotations ---------------------------------------------
    op.create_table(
        "feedback_annotations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "action",
            postgresql.ENUM(
                *FEEDBACK_ACTION_VALUES, name="feedback_action", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_feedback_annotations_action", "feedback_annotations", ["action"]
    )
    op.create_index(
        "ix_feedback_annotations_created_at", "feedback_annotations", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("feedback_annotations")
    op.drop_table("memory_edges")
    op.drop_table("canonical_faqs")
    op.drop_table("cluster_members")
    op.execute("DROP INDEX IF EXISTS ix_semantic_clusters_centroid_cosine;")
    op.drop_table("semantic_clusters")
    op.execute("DROP INDEX IF EXISTS ix_embeddings_vector_cosine;")
    op.drop_table("embeddings")
    op.drop_table("extracted_questions")
    op.drop_table("utterances")
    op.drop_table("calls")

    bind = op.get_bind()
    for name in (
        "edge_relation",
        "feedback_action",
        "question_type",
        "call_status",
        "intent",
        "language",
        "speaker",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
