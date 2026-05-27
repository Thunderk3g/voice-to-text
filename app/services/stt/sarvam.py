"""
Sarvam.ai STT integration.

Sarvam's REST ``/speech-to-text`` (saarika/saaras family) has a ~30-second
synchronous envelope. Insurance calls are routinely several minutes long,
so this module chunks the audio on silence boundaries, transcribes each
chunk in parallel, and stitches the segments back together with absolute
timestamps. Speaker labels are assigned afterwards in
``app.services.stt.speaker_heuristic``.

The class deliberately mirrors the smaller integration in
``compliance-agent-poc/backend/app/services/stt/sarvam.py`` (single-call
chat-turn use case) — same SDK initialisation pattern, same TLS escape
hatch flag name — so operators familiar with that codebase do not need to
re-learn the wiring.

If ``settings.stt_provider != "sarvam"`` or no API key is configured, the
client raises ``SarvamConfigError`` at construction; the worker pipeline
treats this as "transcript-only mode" and refuses to accept audio ingests.
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.utils.lang import detect_language

logger = structlog.get_logger(__name__)


_MIME_EXT: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/flac": "flac",
}


def _ext_for(mime_type: str) -> str:
    return _MIME_EXT.get((mime_type or "").lower(), "wav")


def _guess_mime(path: str) -> str:
    """Fast extension-based MIME guess. Good enough for Sarvam dispatch."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    rev = {v: k for k, v in _MIME_EXT.items()}
    return rev.get(ext, "audio/wav")


class SarvamConfigError(RuntimeError):
    """Raised when Sarvam STT is configured incorrectly. Caller decides whether
    to fail closed or to fall back to a transcript-only ingest path."""


# ----------------------------------------------------------------------------
# Public dataclasses
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class ChunkTranscript:
    """One Sarvam-transcribed chunk with absolute (wall-clock) timestamps."""

    start_ts: float
    end_ts: float
    text: str
    language: Language


# ----------------------------------------------------------------------------
# SarvamTranscriber
# ----------------------------------------------------------------------------
class SarvamTranscriber:
    """Wraps the ``sarvamai`` async SDK with chunking + stitching.

    Typical usage::

        svc = SarvamTranscriber()
        utterances = await svc.transcribe_file(
            call_id=call.id,
            audio_path="/tmp/call.wav",
        )
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if self._settings.stt_provider != "sarvam":
            raise SarvamConfigError(
                f"STT_PROVIDER={self._settings.stt_provider!r} — Sarvam disabled."
            )
        api_key = self._settings.sarvam_api_key.get_secret_value()
        if not api_key:
            raise SarvamConfigError(
                "SARVAM_API_KEY is not set; cannot initialise Sarvam STT."
            )
        self._api_key = api_key

    # ------------------------------------------------------------------ Public
    async def transcribe_file(
        self,
        *,
        call_id: UUID,
        audio_path: str,
    ) -> list[UtteranceSchema]:
        """Transcribe an entire audio file and return diarized utterances.

        Returns ``UtteranceSchema`` rows with ``speaker=Speaker.UNKNOWN``;
        a downstream heuristic assigns AGENT/CUSTOMER labels (the worker
        glues that in).
        """
        chunks = await self._chunk_audio(audio_path)
        if not chunks:
            logger.warning("sarvam.no_audio_chunks", audio_path=audio_path)
            return []

        # Transcribe chunks concurrently, but bound parallelism to avoid
        # hammering Sarvam at 10x the rate (their default rate limits are
        # generous, but not unlimited).
        sem = asyncio.Semaphore(4)

        async def _run(idx_chunk: tuple[int, _AudioChunk]) -> ChunkTranscript | None:
            idx, ch = idx_chunk
            async with sem:
                try:
                    text, lang = await self._transcribe_bytes(
                        ch.data, mime_type="audio/wav", filename=f"chunk_{idx:03d}"
                    )
                except Exception as exc:  # noqa: BLE001 — log + drop the bad chunk
                    logger.warning(
                        "sarvam.chunk_failed",
                        idx=idx,
                        error=str(exc),
                        start_ts=ch.start_ts,
                        end_ts=ch.end_ts,
                    )
                    return None
                if not text.strip():
                    return None
                language = (
                    self._language_from_code(lang) if lang else detect_language(text)
                )
                return ChunkTranscript(
                    start_ts=ch.start_ts,
                    end_ts=ch.end_ts,
                    text=text.strip(),
                    language=language,
                )

        results = await asyncio.gather(*(_run(c) for c in enumerate(chunks)))
        transcripts = [r for r in results if r is not None]
        transcripts.sort(key=lambda t: t.start_ts)

        utterances: list[UtteranceSchema] = [
            UtteranceSchema(
                call_id=call_id,
                speaker=Speaker.UNKNOWN,
                start_ts=t.start_ts,
                end_ts=t.end_ts,
                text=t.text,
                language=t.language,
                confidence=0.85,  # Sarvam doesn't expose per-chunk confidence; conservative default.
            )
            for t in transcripts
        ]
        logger.info(
            "sarvam.transcribe_done",
            n_chunks=len(chunks),
            n_utterances=len(utterances),
            audio_path=audio_path,
        )
        return utterances

    # ------------------------------------------------------------------ Internals
    async def _transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str,
        filename: str = "audio",
    ) -> tuple[str, str | None]:
        """One sync transcribe call. Returns (transcript, language_code|None)."""
        try:
            from sarvamai import AsyncSarvamAI
        except ImportError as exc:  # pragma: no cover — env problem
            raise SarvamConfigError(
                "sarvamai package not installed. Run `pip install -r requirements.txt`."
            ) from exc

        ext = _ext_for(mime_type)
        named = f"{filename}.{ext}"

        client_kwargs: dict[str, Any] = {"api_subscription_key": self._api_key}
        # The compliance-agent variant supports an insecure-TLS escape hatch
        # for corporate MITM proxies. v2t does not surface that flag (yet) —
        # if you need it, copy the same env-driven branch from there.
        client_kwargs.setdefault(
            "httpx_client",
            httpx.AsyncClient(timeout=self._settings.sarvam_request_timeout_s),
        )

        client = AsyncSarvamAI(**client_kwargs)
        try:
            resp = await client.speech_to_text.transcribe(
                file=(named, io.BytesIO(audio_bytes), mime_type),
                model=self._settings.sarvam_stt_model,
                language_code=self._settings.sarvam_language_code,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    maybe = close()
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:  # pragma: no cover — best-effort
                    logger.debug("sarvam.client_close_failed", exc_info=True)

        return _extract_transcript_fields(resp)

    # ----------------------------------------------------------------
    async def _chunk_audio(self, audio_path: str) -> list["_AudioChunk"]:
        """Split the file on silence into Sarvam-sized chunks.

        Heavy IO + audio decoding runs in a thread so the event loop stays
        responsive.
        """
        return await asyncio.to_thread(_split_on_silence_sync, audio_path, self._settings)

    # ----------------------------------------------------------------
    @staticmethod
    def _language_from_code(code: str | None) -> Language:
        if not code:
            return Language.OTHER
        head = code.split("-")[0].lower()
        return {
            "hi": Language.HINDI,
            "en": Language.ENGLISH,
            "ta": Language.TAMIL,
            "te": Language.TELUGU,
            "mr": Language.OTHER,
            "kn": Language.OTHER,
            "ml": Language.OTHER,
            "bn": Language.OTHER,
            "gu": Language.OTHER,
            "pa": Language.OTHER,
        }.get(head, Language.OTHER)


# ----------------------------------------------------------------------------
# Audio chunking
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class _AudioChunk:
    start_ts: float   # seconds from start of file
    end_ts: float
    data: bytes       # WAV-encoded mono PCM at 16 kHz


def _split_on_silence_sync(audio_path: str, settings: Settings) -> list[_AudioChunk]:
    """Decode an audio file with pydub and slice it into ~25 s chunks.

    Prefers silence-aware split points to avoid clipping words. Falls back
    to fixed-window slicing when pydub's silence detector returns nothing
    (e.g. very dense audio with no detectable silences).
    """
    try:
        from pydub import AudioSegment, silence
    except ImportError as exc:  # pragma: no cover
        raise SarvamConfigError(
            "pydub is not installed. Run `pip install -r requirements.txt`. "
            "ffmpeg must also be installed on the host."
        ) from exc

    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_channels(1).set_frame_rate(16_000)
    duration_ms = len(audio)
    target_ms = settings.sarvam_chunk_duration_s * 1000
    overlap_ms = settings.sarvam_chunk_overlap_ms

    if duration_ms <= target_ms:
        return [_export(_AudioChunk(0.0, duration_ms / 1000.0, b""), audio)]

    # 1) Find silence midpoints to use as split candidates.
    silence_ranges = silence.detect_silence(
        audio, min_silence_len=400, silence_thresh=audio.dBFS - 16
    )
    silence_mids = [(a + b) // 2 for a, b in silence_ranges]

    cuts: list[int] = [0]
    cursor = 0
    while cursor + target_ms < duration_ms:
        ideal = cursor + target_ms
        # Pick the silence midpoint closest to `ideal` within ±5 s; else hard-cut.
        candidates = [
            s for s in silence_mids if abs(s - ideal) <= 5_000 and s > cursor + 5_000
        ]
        next_cut = min(candidates, key=lambda s: abs(s - ideal)) if candidates else ideal
        cuts.append(next_cut)
        cursor = next_cut
    cuts.append(duration_ms)

    out: list[_AudioChunk] = []
    for start_ms, end_ms in zip(cuts, cuts[1:]):
        a = max(0, start_ms - overlap_ms if start_ms > 0 else 0)
        b = min(duration_ms, end_ms + overlap_ms)
        slice_ = audio[a:b]
        out.append(
            _export(
                _AudioChunk(a / 1000.0, b / 1000.0, b""),
                slice_,
            )
        )
    return out


def _export(meta: _AudioChunk, segment) -> _AudioChunk:
    """Render an AudioSegment to a WAV bytes payload and wrap in _AudioChunk."""
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return _AudioChunk(start_ts=meta.start_ts, end_ts=meta.end_ts, data=buf.getvalue())


# ----------------------------------------------------------------------------
# Response shape adapter (mirrors compliance-agent helper)
# ----------------------------------------------------------------------------
def _extract_transcript_fields(resp: Any) -> tuple[str, str | None]:
    if resp is None:
        return ("", None)
    if isinstance(resp, dict):
        return (resp.get("transcript") or "", resp.get("language_code"))
    return (getattr(resp, "transcript", "") or "", getattr(resp, "language_code", None))


__all__ = [
    "SarvamTranscriber",
    "SarvamConfigError",
    "ChunkTranscript",
]
