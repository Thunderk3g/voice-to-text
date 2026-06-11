"""Unit tests: batch-reconciliation persistence keeps frequency consistent."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from app.services import factories


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.statements.append((str(clause), params or {}))


async def test_dissolved_cluster_frequency_recounted(monkeypatch) -> None:
    recorder = _RecordingSession()

    @contextmanager
    def _fake_sync_session():
        yield recorder

    monkeypatch.setattr(factories, "sync_session", _fake_sync_session)

    cid = uuid4()
    await factories._persist_batch_async([], [], [cid])

    dissolved = [
        (sql, params)
        for sql, params in recorder.statements
        if "is_stable" in sql.lower()
    ]
    assert len(dissolved) == 1
    sql, params = dissolved[0]
    # The same UPDATE must recount frequency from cluster_members,
    # scoped to the dissolved cluster's id.
    assert "frequency" in sql.lower()
    assert "count(*)" in sql.lower()
    assert params["id"] == str(cid)
