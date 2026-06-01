"""Unit tests for the STT provider factory ``make_transcriber``."""

from __future__ import annotations

import pytest

import app.services.stt as stt_pkg
from app.core.config import Settings


def _patch_settings(monkeypatch, **overrides):
    settings = Settings(**overrides)
    monkeypatch.setattr(stt_pkg, "get_settings", lambda: settings)
    return settings


def test_factory_returns_sarvam(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="sarvam", sarvam_api_key="dummy-key")
    from app.services.stt.sarvam import SarvamTranscriber

    svc = stt_pkg.make_transcriber()
    assert isinstance(svc, SarvamTranscriber)


def test_factory_returns_whisper(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="whisper")
    from app.services.stt.whisper import WhisperTranscriber

    svc = stt_pkg.make_transcriber()
    assert isinstance(svc, WhisperTranscriber)


def test_factory_none_raises(monkeypatch):
    _patch_settings(monkeypatch, stt_provider="none")
    with pytest.raises(RuntimeError):
        stt_pkg.make_transcriber()


def test_factory_provider_override_wins_over_settings(monkeypatch):
    # Global setting is sarvam, but an explicit override selects whisper.
    _patch_settings(monkeypatch, stt_provider="sarvam", sarvam_api_key="dummy-key")
    from app.services.stt.whisper import WhisperTranscriber

    svc = stt_pkg.make_transcriber(provider="whisper")
    assert isinstance(svc, WhisperTranscriber)


def test_factory_no_override_honors_settings(monkeypatch):
    # With no override, the configured provider (sarvam) is used.
    _patch_settings(monkeypatch, stt_provider="sarvam", sarvam_api_key="dummy-key")
    from app.services.stt.sarvam import SarvamTranscriber

    svc = stt_pkg.make_transcriber()
    assert isinstance(svc, SarvamTranscriber)
