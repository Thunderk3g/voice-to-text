"""
Sarvam.ai STT integration — Batch API with diarization.

Long calls go through Sarvam's **Batch STT job API** (create job → upload →
start → poll → download), which accepts files up to two hours and returns a
``diarized_transcript`` with per-entry speaker labels — no more slicing the
audio into 30-second sync chunks, and no more silently dropped chunks when
one request fails. Clips that fit the 30-second sync envelope still use the
cheaper synchronous ``/speech-to-text`` call (which has no diarization; the
downstream heuristic labels speakers).

Every HTTP interaction acquires a key from
:class:`app.services.stt.key_pool.SarvamKeyPool` and reports the outcome
back, so rate-limited accounts rotate out transparently. A batch job sticks
to the key that created it (job state is per account); when that key gets
benched mid-job the whole job restarts on the next key.

Failures are loud by design: any unrecoverable error propagates to the
worker task, which marks the call FAILED with the error message persisted —
the UI shows *why*, instead of a quietly truncated transcript.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.services.stt.key_pool import (
    KeyAction,
    SarvamKeyPool,
    classify_sarvam_error,
    error_code_from_body,
    mask_key,
)
from app.utils.lang import detect_language

logger = structlog.get_logger(__name__)

#: Sarvam's synchronous /speech-to-text envelope (seconds).
_SYNC_MAX_S = 30.0

#: Cap for 5xx retries within one key (rotations are paced by the pool itself).
_MAX_SERVER_RETRIES = 5


class SarvamConfigError(RuntimeError):
    """Raised when Sarvam STT is configured incorrectly. Caller decides whether
    to fail closed or to fall back to a transcript-only ingest path."""


class SarvamJobFailedError(RuntimeError):
    """A batch job (or one of its files) ended in a failed state on Sarvam's side."""


def _extract_status(exc: BaseException) -> tuple[int | None, str | None]:
    """Pull (http_status, sarvam error.code) out of an SDK/httpx exception."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = exc.response.json()
        except Exception:  # noqa: BLE001 — body may not be JSON
            body = None
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return status, error_code_from_body(body)


class SarvamTranscriber:
    """Sarvam STT with key rotation: batch (diarized) for long audio, sync for clips.

    Typical usage::

        svc = SarvamTranscriber()
        utterances = await svc.transcribe_file(call_id=call.id, audio_path="/tmp/call.wav")
    """

    def __init__(
        self,
        settings: Settings | None = None,
        pool: SarvamKeyPool | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if self._settings.stt_provider != "sarvam":
            raise SarvamConfigError(
                f"STT_PROVIDER={self._settings.stt_provider!r} — Sarvam disabled."
            )
        if not self._settings.sarvam_key_list():
            raise SarvamConfigError(
                "No Sarvam keys configured — set SARVAM_API_KEYS (or SARVAM_API_KEY)."
            )
        self._pool = pool  # built lazily so construction never touches Redis

    @property
    def pool(self) -> SarvamKeyPool:
        if self._pool is None:
            self._pool = SarvamKeyPool(self._settings)
        return self._pool

    # ------------------------------------------------------------------ Public
    async def transcribe_file(
        self,
        *,
        call_id: UUID,
        audio_path: str,
    ) -> list[UtteranceSchema]:
        """Transcribe a whole file. Long audio returns diarized utterances
        (``speaker_id`` set, ``speaker=UNKNOWN`` until role mapping); short
        clips return a single undiarized utterance."""
        duration_s = await asyncio.to_thread(_probe_duration_s, audio_path)

        if duration_s <= _SYNC_MAX_S:
            utterances = await self._with_key_rotation(
                lambda key: self._transcribe_sync(key, call_id, audio_path, duration_s)
            )
        else:
            utterances = await self._with_key_rotation(
                lambda key: self._transcribe_batch(key, call_id, audio_path, duration_s)
            )

        logger.info(
            "sarvam.transcribe_done",
            duration_s=round(duration_s, 1),
            n_utterances=len(utterances),
            diarized=any(u.speaker_id for u in utterances),
            audio_path=audio_path,
        )
        return utterances

    # ------------------------------------------------------------------ Key rotation
    async def _with_key_rotation(self, fn) -> Any:
        """Run ``fn(key)`` with pool-driven retry.

        Rate-limited keys cool down and the call moves to the next key;
        invalid/exhausted keys are benched; 5xx retries the same key a few
        times. Anything else propagates — those are real errors the call
        must surface.
        """
        server_retries = 0
        while True:
            key = await self.pool.acquire()
            try:
                result = await fn(key)
            except Exception as exc:  # noqa: BLE001 — classified below
                status, code = _extract_status(exc)
                if status is None:
                    raise
                action = classify_sarvam_error(status, code)
                logger.warning(
                    "sarvam.request_error",
                    key=mask_key(key),
                    status=status,
                    code=code,
                    action=str(action),
                )
                if action is KeyAction.ROTATE_COOLDOWN:
                    await self.pool.report_rate_limited(key)
                    continue
                if action is KeyAction.DISABLE_QUOTA:
                    await self.pool.report_quota_exhausted(key)
                    continue
                if action is KeyAction.DISABLE_INVALID:
                    await self.pool.report_invalid(key)
                    continue
                if action is KeyAction.RETRY_BACKOFF:
                    server_retries += 1
                    if server_retries > _MAX_SERVER_RETRIES:
                        raise
                    await asyncio.sleep(min(2.0**server_retries, 60.0))
                    continue
                raise
            await self.pool.report_success(key)
            return result

    # ------------------------------------------------------------------ Sync path (≤30 s)
    async def _transcribe_sync(
        self, key: str, call_id: UUID, audio_path: str, duration_s: float
    ) -> list[UtteranceSchema]:
        client = self._make_client(key)
        try:
            with open(audio_path, "rb") as fh:
                resp = await client.speech_to_text.transcribe(
                    file=(os.path.basename(audio_path), fh, _guess_mime(audio_path)),
                    model=self._settings.sarvam_stt_model,
                    language_code=self._settings.sarvam_language_code,
                )
        finally:
            await _close_client(client)

        text = (getattr(resp, "transcript", "") or "").strip()
        lang_code = getattr(resp, "language_code", None)
        if not text:
            return []
        language = _language_from_code(lang_code) if lang_code else detect_language(text)
        return [
            UtteranceSchema(
                call_id=call_id,
                speaker=Speaker.UNKNOWN,
                start_ts=0.0,
                end_ts=duration_s,
                text=text,
                language=language,
                confidence=0.85,  # Sarvam exposes no confidence; conservative default.
            )
        ]

    # ------------------------------------------------------------------ Batch path
    async def _transcribe_batch(
        self, key: str, call_id: UUID, audio_path: str, duration_s: float
    ) -> list[UtteranceSchema]:
        client = self._make_client(key)
        try:
            job = await client.speech_to_text_job.create_job(
                model=self._settings.sarvam_stt_model,
                with_diarization=True,
                with_timestamps=True,
                language_code=self._settings.sarvam_language_code,
                num_speakers=self._settings.sarvam_num_speakers,
            )
            logger.info(
                "sarvam.batch_job_created",
                job_id=getattr(job, "job_id", None),
                key=mask_key(key),
            )
            await job.upload_files(file_paths=[audio_path])
            await job.start()
            status = await job.wait_until_complete(
                poll_interval=self._settings.sarvam_batch_poll_interval_s,
                timeout=self._settings.sarvam_batch_timeout_s,
            )

            state = str(getattr(status, "job_state", "")).lower()
            if state != "completed":
                raise SarvamJobFailedError(
                    f"Sarvam batch job ended in state {state or 'unknown'!r}: "
                    f"{getattr(status, 'error_message', None) or 'no error message'}"
                )

            with tempfile.TemporaryDirectory(prefix="sarvam_batch_") as out_dir:
                await job.download_outputs(output_dir=out_dir)
                payloads = _read_output_jsons(out_dir)
        finally:
            await _close_client(client)

        if not payloads:
            raise SarvamJobFailedError(
                "Sarvam batch job completed but produced no output files."
            )
        utterances: list[UtteranceSchema] = []
        for payload in payloads:
            utterances.extend(_utterances_from_batch_output(call_id, payload, duration_s))
        utterances.sort(key=lambda u: u.start_ts)
        return utterances

    # ------------------------------------------------------------------ Client
    def _make_client(self, key: str):
        try:
            from sarvamai import AsyncSarvamAI
        except ImportError as exc:  # pragma: no cover — env problem
            raise SarvamConfigError(
                "sarvamai package not installed. Run `pip install -r requirements.txt`."
            ) from exc
        return AsyncSarvamAI(
            api_subscription_key=key,
            httpx_client=httpx.AsyncClient(
                timeout=self._settings.sarvam_request_timeout_s
            ),
        )


async def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:  # pragma: no cover — best-effort
            logger.debug("sarvam.client_close_failed", exc_info=True)


# ----------------------------------------------------------------------------
# Batch output parsing
# ----------------------------------------------------------------------------
def _read_output_jsons(out_dir: str) -> list[dict]:
    payloads = []
    for path in sorted(Path(out_dir).glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            payloads.append(json.load(fh))
    return payloads


def _utterances_from_batch_output(
    call_id: UUID, payload: dict, duration_s: float
) -> list[UtteranceSchema]:
    """Map one batch output JSON to utterance rows.

    Prefers ``diarized_transcript.entries`` (speaker-labelled segments);
    falls back to the flat ``transcript`` when diarization returned nothing.
    """
    file_lang = payload.get("language_code")
    diarized = payload.get("diarized_transcript") or {}
    entries = diarized.get("entries") or []

    utterances: list[UtteranceSchema] = []
    for entry in entries:
        text = (entry.get("transcript") or "").strip()
        if not text:
            continue
        speaker_id = entry.get("speaker_id")
        utterances.append(
            UtteranceSchema(
                call_id=call_id,
                speaker=Speaker.UNKNOWN,
                speaker_id=str(speaker_id) if speaker_id is not None else None,
                start_ts=float(entry.get("start_time_seconds") or 0.0),
                end_ts=float(entry.get("end_time_seconds") or 0.0),
                text=text,
                language=_language_from_code(file_lang)
                if file_lang
                else detect_language(text),
                confidence=0.85,
            )
        )
    if utterances:
        return utterances

    # Diarization came back empty — keep the full transcript rather than nothing.
    text = (payload.get("transcript") or "").strip()
    if not text:
        return []
    logger.warning("sarvam.batch_no_diarization", call_id=str(call_id))
    return [
        UtteranceSchema(
            call_id=call_id,
            speaker=Speaker.UNKNOWN,
            start_ts=0.0,
            end_ts=duration_s,
            text=text,
            language=_language_from_code(file_lang) if file_lang else detect_language(text),
            confidence=0.85,
        )
    ]


# ----------------------------------------------------------------------------
# Audio + language helpers
# ----------------------------------------------------------------------------
def _probe_duration_s(audio_path: str) -> float:
    try:
        from pydub import AudioSegment
    except ImportError as exc:  # pragma: no cover
        raise SarvamConfigError(
            "pydub is not installed. Run `pip install -r requirements.txt`. "
            "ffmpeg must also be installed on the host."
        ) from exc
    return len(AudioSegment.from_file(audio_path)) / 1000.0


_MIME_BY_EXT: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "flac": "audio/flac",
}


def _guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return _MIME_BY_EXT.get(ext, "audio/wav")


def _language_from_code(code: str | None) -> Language:
    if not code:
        return Language.OTHER
    head = code.split("-")[0].lower()
    return {
        "hi": Language.HINDI,
        "en": Language.ENGLISH,
        "ta": Language.TAMIL,
        "te": Language.TELUGU,
    }.get(head, Language.OTHER)


__all__ = [
    "SarvamTranscriber",
    "SarvamConfigError",
    "SarvamJobFailedError",
]
