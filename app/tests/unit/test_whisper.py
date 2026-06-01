"""Unit tests for ``app.services.stt.whisper.WhisperTranscriber``.

``faster_whisper.WhisperModel`` is patched so NO model weights are ever
downloaded or loaded. We feed fake segments / info objects and assert the
mapping onto ``UtteranceSchema`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

import app.services.stt.whisper as whisper_mod
from app.models.enums import Language, Speaker
from app.services.stt.whisper import WhisperTranscriber


@dataclass
class _FakeSegment:
    start: float
    end: float
    text: str
    avg_logprob: float = -0.2


@dataclass
class _FakeInfo:
    language: str = "hi"


class _FakeModel:
    """Stand-in for faster_whisper.WhisperModel."""

    def __init__(self, segments, info):
        self._segments = segments
        self._info = info
        self.transcribe_calls: list[dict] = []

    def transcribe(self, audio_path, **kwargs):
        self.transcribe_calls.append({"audio_path": audio_path, **kwargs})
        return iter(self._segments), self._info


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level model singleton is cleared between tests."""
    whisper_mod._reset_model_cache()
    yield
    whisper_mod._reset_model_cache()


def _patch_model(monkeypatch, fake_model: _FakeModel) -> dict:
    """Patch the lazy faster_whisper.WhisperModel constructor."""
    captured: dict = {}

    def _ctor(model, device=None, compute_type=None, **kw):
        captured["model"] = model
        captured["device"] = device
        captured["compute_type"] = compute_type
        return fake_model

    monkeypatch.setattr(whisper_mod, "_load_whisper_model_class", lambda: _ctor)
    return captured


async def test_transcribe_maps_segments_to_utterances(monkeypatch):
    call_id = uuid4()
    segments = [
        _FakeSegment(start=0.0, end=2.5, text=" Namaste, policy ke baare mein.", avg_logprob=-0.1),
        _FakeSegment(start=2.5, end=5.0, text=" Aapka premium kitna hai?", avg_logprob=-0.3),
    ]
    fake = _FakeModel(segments, _FakeInfo(language="hi"))
    _patch_model(monkeypatch, fake)

    svc = WhisperTranscriber()
    out = await svc.transcribe_file(call_id=call_id, audio_path="/tmp/call.wav")

    assert len(out) == 2
    assert all(u.call_id == call_id for u in out)
    assert all(u.speaker == Speaker.UNKNOWN for u in out)
    assert out[0].start_ts == 0.0
    assert out[0].end_ts == 2.5
    assert out[0].text == "Namaste, policy ke baare mein."  # stripped
    assert out[0].language == Language.HINDI
    assert out[1].start_ts == 2.5
    assert 0.0 <= out[0].confidence <= 1.0


async def test_transcribe_skips_empty_text_segments(monkeypatch):
    call_id = uuid4()
    segments = [
        _FakeSegment(start=0.0, end=1.0, text="   "),  # empty -> skip
        _FakeSegment(start=1.0, end=2.0, text="Real text here."),
    ]
    fake = _FakeModel(segments, _FakeInfo(language="en"))
    _patch_model(monkeypatch, fake)

    svc = WhisperTranscriber()
    out = await svc.transcribe_file(call_id=call_id, audio_path="/tmp/call.wav")

    assert len(out) == 1
    assert out[0].text == "Real text here."
    assert out[0].language == Language.ENGLISH


async def test_language_auto_passes_none(monkeypatch):
    """whisper_language='' must translate to language=None in transcribe()."""
    from app.core.config import Settings

    call_id = uuid4()
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hello")], _FakeInfo(language="en"))
    _patch_model(monkeypatch, fake)

    settings = Settings(stt_provider="whisper", whisper_language="")
    svc = WhisperTranscriber(settings=settings)
    await svc.transcribe_file(call_id=call_id, audio_path="/tmp/call.wav")

    assert fake.transcribe_calls[0]["language"] is None
    assert fake.transcribe_calls[0]["vad_filter"] is True


async def test_language_explicit_is_forwarded(monkeypatch):
    from app.core.config import Settings

    call_id = uuid4()
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hello")], _FakeInfo(language="en"))
    _patch_model(monkeypatch, fake)

    settings = Settings(stt_provider="whisper", whisper_language="hi")
    svc = WhisperTranscriber(settings=settings)
    await svc.transcribe_file(call_id=call_id, audio_path="/tmp/call.wav")

    assert fake.transcribe_calls[0]["language"] == "hi"


async def test_unmapped_language_falls_back_to_detect(monkeypatch):
    """An info.language Whisper code we don't map (e.g. 'mr') falls back to
    detect_language(text), which keys off the script."""
    call_id = uuid4()
    # Devanagari text -> detect_language returns HINDI even though info says 'mr'.
    segments = [_FakeSegment(0.0, 1.0, "मेरा प्रीमियम कितना है")]
    fake = _FakeModel(segments, _FakeInfo(language="mr"))
    _patch_model(monkeypatch, fake)

    svc = WhisperTranscriber()
    out = await svc.transcribe_file(call_id=call_id, audio_path="/tmp/call.wav")

    assert len(out) == 1
    assert out[0].language == Language.HINDI


async def test_model_is_cached_singleton(monkeypatch):
    """The WhisperModel constructor must be invoked at most once across calls."""
    fake = _FakeModel([_FakeSegment(0.0, 1.0, "hi")], _FakeInfo(language="en"))
    ctor_calls = {"n": 0}

    def _ctor(model, device=None, compute_type=None, **kw):
        ctor_calls["n"] += 1
        return fake

    monkeypatch.setattr(whisper_mod, "_load_whisper_model_class", lambda: _ctor)

    svc = WhisperTranscriber()
    await svc.transcribe_file(call_id=uuid4(), audio_path="/tmp/a.wav")
    await svc.transcribe_file(call_id=uuid4(), audio_path="/tmp/b.wav")

    assert ctor_calls["n"] == 1
