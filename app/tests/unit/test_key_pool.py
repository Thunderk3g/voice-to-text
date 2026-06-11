"""Unit tests for the Sarvam rotating key pool."""

from __future__ import annotations

import asyncio
import time

import pytest
from fakeredis.aioredis import FakeRedis

from app.core.config import Settings
from app.services.stt.key_pool import (
    PERMANENT,
    AllKeysDisabledError,
    KeyAction,
    SarvamKeyPool,
    classify_sarvam_error,
    error_code_from_body,
    mask_key,
)


# ----------------------------------------------------------------------------
# classify_sarvam_error
# ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (429, "rate_limit_exceeded_error", KeyAction.ROTATE_COOLDOWN),
        (429, None, KeyAction.ROTATE_COOLDOWN),
        (429, "insufficient_quota_error", KeyAction.DISABLE_QUOTA),
        (403, "invalid_api_key_error", KeyAction.DISABLE_INVALID),
        (403, None, KeyAction.DISABLE_INVALID),
        (500, None, KeyAction.RETRY_BACKOFF),
        (503, "rate_limit_exceeded_error", KeyAction.RETRY_BACKOFF),
        (400, "invalid_request_error", KeyAction.FAIL),
        (422, None, KeyAction.FAIL),
        (None, None, KeyAction.FAIL),
    ],
)
def test_classify_sarvam_error(status, code, expected):
    assert classify_sarvam_error(status, code) is expected


def test_error_code_from_body_variants():
    assert error_code_from_body({"error": {"code": "rate_limit_exceeded_error"}}) == (
        "rate_limit_exceeded_error"
    )
    assert error_code_from_body('{"error": {"code": "insufficient_quota_error"}}') == (
        "insufficient_quota_error"
    )
    assert error_code_from_body(None) is None
    assert error_code_from_body("not json") is None
    assert error_code_from_body({"message": "nope"}) is None


def test_mask_key_never_leaks_middle():
    key = "sk_n3njdc2g_WIJem4NN4DXcKyi9XGZR7hKJ"
    masked = mask_key(key)
    assert key not in masked
    assert masked.startswith("sk_n3njdc2")
    assert masked.endswith("7hKJ")
    assert mask_key("short") == "****"


# ----------------------------------------------------------------------------
# SarvamKeyPool
# ----------------------------------------------------------------------------
KEYS = ["sk_aaaaaaaa_AAAAAAAAAAAAAAAAAAAAAAAA",
        "sk_bbbbbbbb_BBBBBBBBBBBBBBBBBBBBBBBB",
        "sk_cccccccc_CCCCCCCCCCCCCCCCCCCCCCCC"]


def _make_pool(**overrides) -> SarvamKeyPool:
    settings = Settings(
        sarvam_api_keys=",".join(KEYS),
        sarvam_key_cooldown_base_s=0.2,
        sarvam_key_cooldown_max_s=0.8,
        **overrides,
    )
    return SarvamKeyPool(settings, redis=FakeRedis(decode_responses=True))


def test_requires_at_least_one_key():
    with pytest.raises(RuntimeError):
        SarvamKeyPool(
            Settings(sarvam_api_keys="", sarvam_api_key=""),
            redis=FakeRedis(decode_responses=True),
        )


def test_single_legacy_key_works():
    settings = Settings(sarvam_api_keys="", sarvam_api_key=KEYS[0])
    pool = SarvamKeyPool(settings, redis=FakeRedis(decode_responses=True))
    key = asyncio.run(pool.acquire())
    assert key == KEYS[0]


def test_round_robin_cycles_all_keys():
    pool = _make_pool()

    async def run():
        return [await pool.acquire() for _ in range(6)]

    got = asyncio.run(run())
    assert set(got[:3]) == set(KEYS)
    assert got[:3] == got[3:6]


def test_rate_limited_key_is_skipped_until_cooldown_expires():
    pool = _make_pool()

    async def run():
        await pool.report_rate_limited(KEYS[1])
        got = [await pool.acquire() for _ in range(4)]
        assert KEYS[1] not in got
        await asyncio.sleep(0.25)  # > 0.2s base cooldown
        got_after = [await pool.acquire() for _ in range(3)]
        assert KEYS[1] in got_after

    asyncio.run(run())


def test_cooldown_grows_exponentially_and_caps():
    pool = _make_pool()

    async def run():
        c1 = await pool.report_rate_limited(KEYS[0])
        c2 = await pool.report_rate_limited(KEYS[0])
        c3 = await pool.report_rate_limited(KEYS[0])
        c4 = await pool.report_rate_limited(KEYS[0])
        assert (c1, c2, c3) == (0.2, 0.4, 0.8)
        assert c4 == 0.8  # capped at sarvam_key_cooldown_max_s

    asyncio.run(run())


def test_success_resets_429_streak():
    pool = _make_pool()

    async def run():
        await pool.report_rate_limited(KEYS[0])
        await pool.report_rate_limited(KEYS[0])
        await pool.report_success(KEYS[0])
        assert await pool.report_rate_limited(KEYS[0]) == 0.2  # back to base

    asyncio.run(run())


def test_invalid_and_quota_keys_are_benched():
    pool = _make_pool()

    async def run():
        await pool.report_invalid(KEYS[0])
        await pool.report_quota_exhausted(KEYS[1])
        got = [await pool.acquire() for _ in range(4)]
        assert set(got) == {KEYS[2]}

    asyncio.run(run())


def test_all_cooling_waits_then_returns():
    pool = _make_pool()

    async def run():
        for k in KEYS:
            await pool.report_rate_limited(k)
        start = time.monotonic()
        key = await pool.acquire()
        waited = time.monotonic() - start
        assert key in KEYS
        assert waited >= 0.1  # actually slept through the cooldown

    asyncio.run(run())


def test_all_benched_raises():
    pool = _make_pool()

    async def run():
        for k in KEYS:
            await pool.report_invalid(k)
        with pytest.raises(AllKeysDisabledError):
            await pool.acquire()

    asyncio.run(run())


def test_statuses_masks_and_labels():
    pool = _make_pool()

    async def run():
        await pool.report_invalid(KEYS[0])
        await pool.report_rate_limited(KEYS[1])
        await pool.report_success(KEYS[2])
        return await pool.statuses()

    statuses = asyncio.run(run())
    by_state = {s.state for s in statuses}
    assert by_state == {"disabled", "cooldown", "healthy"}
    for s in statuses:
        assert all(k not in s.masked for k in KEYS)
    healthy = next(s for s in statuses if s.state == "healthy")
    assert healthy.ok_count == 1
