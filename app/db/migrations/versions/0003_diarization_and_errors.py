"""Add utterances.speaker_id and calls.error_message.

Supports the Sarvam Batch STT migration:
  * utterances.speaker_id — raw diarization label ("0", "1", ...) returned
    by Sarvam batch / pyannote, kept alongside the mapped AGENT/CUSTOMER
    speaker enum so role mapping stays auditable.
  * calls.error_message — human-readable failure reason persisted when a
    pipeline stage fails, so the UI can show *why* instead of a bare
    "failed" status.

Both columns are nullable; safe on existing rows.

Revision ID: 0003_diarization_and_errors
Revises: 0002_call_trace_and_overrides
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_diarization_and_errors"
down_revision = "0002_call_trace_and_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "utterances",
        sa.Column("speaker_id", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "calls",
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calls", "error_message")
    op.drop_column("utterances", "speaker_id")
