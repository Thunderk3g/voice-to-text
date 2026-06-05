"""
Unit tests: start_call_pipeline dispatches ONLY the head stage, and routes
slow local-Whisper transcription to the dedicated heavy queue so it can't
starve the fast lane.

The pipeline advances via each task's own ``_next(...)`` handoff, so
``start_call_pipeline`` dispatches a single head signature (a Celery ``chain``
would be a redundant second driver whose mutable signatures prepend the prior
task's result -> "too many positional arguments").
"""

from __future__ import annotations

from unittest.mock import MagicMock

import app.workers.pipelines as pipelines
from app.workers.pipelines import STT_HEAVY_QUEUE


def _patch_signature(monkeypatch) -> tuple[list[str], list[dict]]:
    """Record (task names created, apply_async kwargs per dispatch)."""
    created: list[str] = []
    apply_kwargs: list[dict] = []

    def fake_signature(name, args=None, **kwargs):
        created.append(name)
        sig = MagicMock()
        sig.task = name

        def _apply_async(**kw):
            apply_kwargs.append(kw)
            return MagicMock(id="task-id-123")

        sig.apply_async.side_effect = _apply_async
        return sig

    monkeypatch.setattr(pipelines.celery_app, "signature", fake_signature)
    return created, apply_kwargs


def test_audio_whisper_routes_to_heavy_queue(monkeypatch) -> None:
    monkeypatch.setattr(pipelines, "_is_transcript", lambda cid: False)
    monkeypatch.setattr(pipelines, "_stt_provider", lambda cid: "whisper")
    created, apply_kwargs = _patch_signature(monkeypatch)

    result = pipelines.start_call_pipeline("call-1")

    assert created == ["v2t.transcribe"]
    assert apply_kwargs == [{"queue": STT_HEAVY_QUEUE}]
    assert result.id == "task-id-123"


def test_audio_sarvam_stays_on_default_lane(monkeypatch) -> None:
    monkeypatch.setattr(pipelines, "_is_transcript", lambda cid: False)
    monkeypatch.setattr(pipelines, "_stt_provider", lambda cid: "sarvam")
    created, apply_kwargs = _patch_signature(monkeypatch)

    pipelines.start_call_pipeline("call-2")

    assert created == ["v2t.transcribe"]
    # No queue override -> default fast lane (apply_async called with no kwargs).
    assert apply_kwargs == [{}]


def test_transcript_head_on_default_lane(monkeypatch) -> None:
    monkeypatch.setattr(pipelines, "_is_transcript", lambda cid: True)
    # _stt_provider must NOT be consulted for transcript uploads.
    monkeypatch.setattr(
        pipelines,
        "_stt_provider",
        lambda cid: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    created, apply_kwargs = _patch_signature(monkeypatch)

    pipelines.start_call_pipeline("call-3")

    assert created == ["v2t._load_transcript"]
    assert apply_kwargs == [{}]
