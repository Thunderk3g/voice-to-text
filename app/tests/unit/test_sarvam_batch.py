"""Unit tests for the Sarvam batch provider: output parsing + key rotation."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.models.enums import Speaker
from app.services.stt.key_pool import AllKeysDisabledError
from app.services.stt.sarvam import (
    SarvamConfigError,
    SarvamTranscriber,
    _extract_status,
    _utterances_from_batch_output,
)

CALL_ID = uuid4()
KEYS = ["sk_aaaaaaaa_AAAAAAAAAAAAAAAAAAAAAAAA",
        "sk_bbbbbbbb_BBBBBBBBBBBBBBBBBBBBBBBB"]


def _settings(**overrides) -> Settings:
    return Settings(
        stt_provider="sarvam",
        sarvam_api_keys=",".join(KEYS),
        **overrides,
    )


# ----------------------------------------------------------------------------
# Batch output parsing
# ----------------------------------------------------------------------------
def test_diarized_entries_become_utterances():
    payload = {
        "language_code": "hi-IN",
        "transcript": "full text",
        "diarized_transcript": {
            "entries": [
                {
                    "transcript": "नमस्ते, बजाज आलियांज़ में आपका स्वागत है",
                    "start_time_seconds": 0.4,
                    "end_time_seconds": 4.1,
                    "speaker_id": "0",
                },
                {
                    "transcript": "mujhe policy details chahiye",
                    "start_time_seconds": 4.5,
                    "end_time_seconds": 7.0,
                    "speaker_id": 1,  # ints must coerce to str
                },
                {"transcript": "   ", "start_time_seconds": 7.0,
                 "end_time_seconds": 7.5, "speaker_id": "0"},  # empty → dropped
            ]
        },
    }
    utts = _utterances_from_batch_output(CALL_ID, payload, duration_s=60.0)
    assert len(utts) == 2
    assert [u.speaker_id for u in utts] == ["0", "1"]
    assert utts[0].start_ts == 0.4 and utts[0].end_ts == 4.1
    assert all(u.speaker is Speaker.UNKNOWN for u in utts)  # roles mapped downstream
    assert utts[0].language.value == "hi"


def test_no_diarization_falls_back_to_flat_transcript():
    payload = {"language_code": "en-IN", "transcript": "hello world",
               "diarized_transcript": {"entries": []}}
    utts = _utterances_from_batch_output(CALL_ID, payload, duration_s=42.0)
    assert len(utts) == 1
    assert utts[0].text == "hello world"
    assert utts[0].speaker_id is None
    assert utts[0].end_ts == 42.0


def test_empty_output_yields_no_utterances():
    assert _utterances_from_batch_output(CALL_ID, {}, duration_s=10.0) == []


# ----------------------------------------------------------------------------
# _extract_status
# ----------------------------------------------------------------------------
class FakeApiError(Exception):
    """Shape-compatible with sarvamai.core.api_error.ApiError."""

    def __init__(self, status_code, body=None):
        super().__init__(f"status_code: {status_code}")
        self.status_code = status_code
        self.body = body


def test_extract_status_from_sdk_error():
    exc = FakeApiError(429, {"error": {"code": "insufficient_quota_error"}})
    assert _extract_status(exc) == (429, "insufficient_quota_error")


def test_extract_status_from_httpx_error():
    resp = httpx.Response(
        403,
        json={"error": {"code": "invalid_api_key_error"}},
        request=httpx.Request("POST", "https://api.sarvam.ai/x"),
    )
    exc = httpx.HTTPStatusError("forbidden", request=resp.request, response=resp)
    assert _extract_status(exc) == (403, "invalid_api_key_error")


def test_extract_status_from_plain_exception():
    assert _extract_status(ValueError("nope")) == (None, None)


# ----------------------------------------------------------------------------
# Key rotation wrapper
# ----------------------------------------------------------------------------
class FakePool:
    def __init__(self, keys):
        self._keys = list(keys)
        self._i = -1
        self.rate_limited: list[str] = []
        self.quota: list[str] = []
        self.invalid: list[str] = []
        self.success: list[str] = []

    async def acquire(self):
        live = [k for k in self._keys if k not in self.invalid and k not in self.quota]
        if not live:
            raise AllKeysDisabledError("all benched")
        self._i += 1
        return live[self._i % len(live)]

    async def report_success(self, key):
        self.success.append(key)

    async def report_rate_limited(self, key):
        self.rate_limited.append(key)
        return 0.0

    async def report_quota_exhausted(self, key):
        self.quota.append(key)

    async def report_invalid(self, key):
        self.invalid.append(key)


def _transcriber(pool) -> SarvamTranscriber:
    return SarvamTranscriber(_settings(), pool=pool)


def test_rotation_on_rate_limit_then_success():
    pool = FakePool(KEYS)
    svc = _transcriber(pool)
    calls: list[str] = []

    async def fn(key):
        calls.append(key)
        if len(calls) == 1:
            raise FakeApiError(429, {"error": {"code": "rate_limit_exceeded_error"}})
        return "ok"

    result = asyncio.run(svc._with_key_rotation(fn))
    assert result == "ok"
    assert pool.rate_limited == [calls[0]]
    assert pool.success == [calls[1]]
    assert calls[0] != calls[1]


def test_invalid_key_benched_then_next_key_used():
    pool = FakePool(KEYS)
    svc = _transcriber(pool)

    async def fn(key):
        if key == KEYS[0]:
            raise FakeApiError(403, {"error": {"code": "invalid_api_key_error"}})
        return key

    result = asyncio.run(svc._with_key_rotation(fn))
    assert result == KEYS[1]
    assert pool.invalid == [KEYS[0]]


def test_quota_exhausted_benched():
    pool = FakePool(KEYS)
    svc = _transcriber(pool)

    async def fn(key):
        if key == KEYS[0]:
            raise FakeApiError(429, {"error": {"code": "insufficient_quota_error"}})
        return key

    assert asyncio.run(svc._with_key_rotation(fn)) == KEYS[1]
    assert pool.quota == [KEYS[0]]


def test_client_error_propagates_without_rotation():
    pool = FakePool(KEYS)
    svc = _transcriber(pool)

    async def fn(key):
        raise FakeApiError(400, {"error": {"code": "invalid_request_error"}})

    with pytest.raises(FakeApiError):
        asyncio.run(svc._with_key_rotation(fn))
    assert pool.rate_limited == [] and pool.invalid == []


def test_server_errors_retry_then_give_up(monkeypatch):
    async def instant_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant_sleep)
    pool = FakePool(KEYS)
    svc = _transcriber(pool)
    attempts = []

    async def fn(key):
        attempts.append(key)
        raise FakeApiError(503, None)

    with pytest.raises(FakeApiError):
        asyncio.run(svc._with_key_rotation(fn))
    assert len(attempts) == 6  # 1 + _MAX_SERVER_RETRIES


def test_all_keys_benched_surfaces_pool_error():
    pool = FakePool(KEYS)
    svc = _transcriber(pool)

    async def fn(key):
        raise FakeApiError(403, None)

    with pytest.raises(AllKeysDisabledError):
        asyncio.run(svc._with_key_rotation(fn))


# ----------------------------------------------------------------------------
# Construction guards
# ----------------------------------------------------------------------------
def test_wrong_provider_raises():
    with pytest.raises(SarvamConfigError):
        SarvamTranscriber(Settings(stt_provider="whisper"))


def test_missing_keys_raise():
    with pytest.raises(SarvamConfigError):
        SarvamTranscriber(
            Settings(stt_provider="sarvam", sarvam_api_keys="", sarvam_api_key="")
        )
