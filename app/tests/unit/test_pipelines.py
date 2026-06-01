"""
Unit tests: start_call_pipeline dispatches ONLY the head stage.

The pipeline advances via each task's own ``_next(...)`` handoff, so
``start_call_pipeline`` must dispatch a single head signature. A Celery
``chain`` would be a redundant second driver whose mutable signatures prepend
the previous task's result, calling the next task with an extra positional arg
(``extract_call() takes 2 positional arguments but 3 were given``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import app.workers.pipelines as pipelines


def _patch_signature(monkeypatch) -> list[str]:
    """Record every task name a signature is created for; return that list."""
    created: list[str] = []

    def fake_signature(name, args=None, **kwargs):
        created.append(name)
        sig = MagicMock()
        sig.task = name
        sig.apply_async.return_value = MagicMock(id="task-id-123")
        return sig

    monkeypatch.setattr(pipelines.celery_app, "signature", fake_signature)
    return created


def test_audio_dispatches_only_transcribe_head(monkeypatch) -> None:
    monkeypatch.setattr(pipelines, "_is_transcript", lambda cid: False)
    created = _patch_signature(monkeypatch)

    result = pipelines.start_call_pipeline("call-1")

    # Exactly one signature, the head — NOT a chain over extract/embed/cluster.
    assert created == ["v2t.transcribe"]
    assert result.id == "task-id-123"


def test_transcript_dispatches_only_loader_head(monkeypatch) -> None:
    monkeypatch.setattr(pipelines, "_is_transcript", lambda cid: True)
    created = _patch_signature(monkeypatch)

    pipelines.start_call_pipeline("call-2")

    assert created == ["v2t._load_transcript"]
