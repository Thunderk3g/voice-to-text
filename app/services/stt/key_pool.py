"""
Rotating Sarvam API-key pool.

Sarvam rate limits are enforced **per account**; the operator provisions one
key per account (SARVAM_API_KEYS, comma-separated) and this pool rotates
round-robin across them so a single rate-limited account never stalls the
pipeline. State lives in Redis because Celery workers are separate
processes/containers and must share key health.

Error policy (see ``classify_sarvam_error``):
  * 429 ``rate_limit_exceeded_error``  -> short exponential cooldown, rotate
  * 429 ``insufficient_quota_error``   -> credits gone; bench key ~24 h
  * 403 ``invalid_api_key_error``      -> dead key; bench permanently
  * 5xx                                -> server blip; retry same key
  * anything else                      -> caller's bug or bad input; fail

When *every* key is cooling down, ``acquire()`` sleeps until the soonest
cooldown lapses. When every key is benched (invalid/exhausted) it raises
``AllKeysDisabledError`` — that is an operator problem, not a retry problem.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_CURSOR_KEY = "sarvam:key_cursor"
_STATE_KEY = "sarvam:key:{kid}"  # hash per key

#: Sentinel for "benched forever" in the ``disabled_until`` field.
PERMANENT = -1.0


class AllKeysDisabledError(RuntimeError):
    """Every key in the pool is invalid or out of credits."""


class KeyAction(StrEnum):
    ROTATE_COOLDOWN = "rotate_cooldown"
    DISABLE_QUOTA = "disable_quota"
    DISABLE_INVALID = "disable_invalid"
    RETRY_BACKOFF = "retry_backoff"
    FAIL = "fail"


def classify_sarvam_error(status_code: int | None, error_code: str | None) -> KeyAction:
    """Map a Sarvam HTTP error to a pool action.

    Both "rate limited" and "out of credits" arrive as HTTP 429 — only the
    body's ``error.code`` distinguishes them.
    """
    if status_code == 403:
        return KeyAction.DISABLE_INVALID
    if status_code == 429:
        if error_code == "insufficient_quota_error":
            return KeyAction.DISABLE_QUOTA
        return KeyAction.ROTATE_COOLDOWN
    if status_code in (500, 502, 503, 504):
        return KeyAction.RETRY_BACKOFF
    return KeyAction.FAIL


def error_code_from_body(body: Any) -> str | None:
    """Pull ``error.code`` out of a Sarvam error response body (dict or JSON str)."""
    if body is None:
        return None
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            return str(code) if code else None
    return None


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def mask_key(key: str) -> str:
    if len(key) <= 12:
        return "****"
    return f"{key[:11]}…{key[-4:]}"


@dataclass(frozen=True)
class KeyStatus:
    """Admin-facing view of one pool key. Never exposes the raw key."""

    masked: str
    state: str  # "healthy" | "cooldown" | "disabled"
    available_at: float | None  # epoch seconds; None when healthy or permanent
    ok_count: int
    err_count: int


class SarvamKeyPool:
    """Round-robin pool over the configured Sarvam keys, state in Redis."""

    def __init__(self, settings: Settings | None = None, redis: Any = None) -> None:
        self._settings = settings or get_settings()
        self._keys = self._settings.sarvam_key_list()
        if not self._keys:
            raise RuntimeError(
                "No Sarvam API keys configured — set SARVAM_API_KEYS (or SARVAM_API_KEY)."
            )
        if redis is None:
            from redis.asyncio import Redis

            redis = Redis.from_url(self._settings.redis_url, decode_responses=True)
        self._redis = redis

    # ------------------------------------------------------------------ acquire
    async def acquire(self) -> str:
        """Return the next healthy key, sleeping through global cooldowns."""
        while True:
            soonest: float | None = None
            for _ in range(len(self._keys)):
                cursor = int(await self._redis.incr(_CURSOR_KEY))
                key = self._keys[cursor % len(self._keys)]
                until = await self._disabled_until(key)
                now = time.time()
                if until is None or (until != PERMANENT and until <= now):
                    return key
                if until != PERMANENT:
                    soonest = until if soonest is None else min(soonest, until)
            if soonest is None:
                raise AllKeysDisabledError(
                    "All Sarvam API keys are disabled (invalid or out of credits). "
                    "Check /admin/keys and the Sarvam dashboard."
                )
            wait = max(0.5, soonest - time.time())
            logger.warning("sarvam.pool_all_cooling", wait_s=round(wait, 1))
            await asyncio.sleep(wait)

    # ------------------------------------------------------------------ reports
    async def report_success(self, key: str) -> None:
        state = _STATE_KEY.format(kid=key_id(key))
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(state, "consecutive_429", 0)
            pipe.hincrby(state, "ok_count", 1)
            await pipe.execute()

    async def report_rate_limited(self, key: str) -> float:
        """Apply an exponential cooldown; returns the cooldown seconds used."""
        state = _STATE_KEY.format(kid=key_id(key))
        n = int(await self._redis.hincrby(state, "consecutive_429", 1))
        cooldown = min(
            self._settings.sarvam_key_cooldown_base_s * (2 ** (n - 1)),
            self._settings.sarvam_key_cooldown_max_s,
        )
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(state, "disabled_until", time.time() + cooldown)
            pipe.hincrby(state, "err_count", 1)
            await pipe.execute()
        logger.warning("sarvam.key_rate_limited", key=mask_key(key), cooldown_s=cooldown)
        return cooldown

    async def report_quota_exhausted(self, key: str) -> None:
        await self._bench(key, time.time() + self._settings.sarvam_quota_disable_s)
        logger.error("sarvam.key_quota_exhausted", key=mask_key(key))

    async def report_invalid(self, key: str) -> None:
        await self._bench(key, PERMANENT)
        logger.error("sarvam.key_invalid", key=mask_key(key))

    # ------------------------------------------------------------------ admin
    async def statuses(self) -> list[KeyStatus]:
        now = time.time()
        out: list[KeyStatus] = []
        for key in self._keys:
            state = await self._redis.hgetall(_STATE_KEY.format(kid=key_id(key)))
            until = float(state.get("disabled_until", 0) or 0)
            if until == PERMANENT:
                label, avail = "disabled", None
            elif until > now:
                # Long benches (quota) read better as "disabled" than "cooldown".
                label = "disabled" if until - now > 2 * self._settings.sarvam_key_cooldown_max_s else "cooldown"
                avail = until
            else:
                label, avail = "healthy", None
            out.append(
                KeyStatus(
                    masked=mask_key(key),
                    state=label,
                    available_at=avail,
                    ok_count=int(state.get("ok_count", 0) or 0),
                    err_count=int(state.get("err_count", 0) or 0),
                )
            )
        return out

    # ------------------------------------------------------------------ internals
    async def _disabled_until(self, key: str) -> float | None:
        raw = await self._redis.hget(_STATE_KEY.format(kid=key_id(key)), "disabled_until")
        return float(raw) if raw is not None else None

    async def _bench(self, key: str, until: float) -> None:
        state = _STATE_KEY.format(kid=key_id(key))
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hset(state, "disabled_until", until)
            pipe.hincrby(state, "err_count", 1)
            await pipe.execute()


__all__ = [
    "SarvamKeyPool",
    "KeyAction",
    "KeyStatus",
    "AllKeysDisabledError",
    "classify_sarvam_error",
    "error_code_from_body",
    "key_id",
    "mask_key",
    "PERMANENT",
]
