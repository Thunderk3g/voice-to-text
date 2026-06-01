"""Add Call.langsmith_trace_id and ExtractedQuestion.human_override.

Two small additive columns to support observability + the feedback loop:
  * calls.langsmith_trace_id — captured from the LangSmith run for the
    extract/embed stage so the dashboard can deep-link into a trace.
  * extracted_questions.human_override — boolean flag flipped by feedback
    tasks (merge / split / relabel) so downstream learning treats those
    rows as gold labels.

Both columns are nullable (or default False) to keep migration safe on
existing rows.

Revision ID: 0002_call_trace_and_overrides
Revises: 0001_initial
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0002_call_trace_and_overrides"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. calls.langsmith_trace_id
    op.add_column(
        "calls",
        sa.Column("langsmith_trace_id", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_calls_langsmith_trace_id",
        "calls",
        ["langsmith_trace_id"],
        unique=False,
    )

    # 2. extracted_questions.human_override
    op.add_column(
        "extracted_questions",
        sa.Column(
            "human_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("extracted_questions", "human_override")
    op.drop_index("ix_calls_langsmith_trace_id", table_name="calls")
    op.drop_column("calls", "langsmith_trace_id")
