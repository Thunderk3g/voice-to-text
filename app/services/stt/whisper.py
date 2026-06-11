"""
Open-source faster-whisper STT integration.

``faster-whisper`` (CTranslate2 backend) runs Whisper locally with no API
key and no per-call HTTP envelope, so — unlike the Sarvam integration — it
handles multi-minute insurance calls natively and does NOT chunk the audio.
The default ``large-v3`` model runs on CPU with ``int8`` compute, which is
the configuration the worker container ships with (Linux, no GPU).

The class deliberately mirrors ``app.services.stt.sarvam.SarvamTranscriber``
so the two providers are duck-type interchangeable behind
``app.services.stt.make_transcriber``: same
``async transcribe_file(*, call_id, audio_path) -> list[UtteranceSchema]``
signature, same ``Speaker.UNKNOWN`` rows (a downstream heuristic assigns
AGENT/CUSTOMER labels), same ``_language_from_code`` mapping + ``detect_language``
fallback.

Loading Whisper weights is expensive, so the model is cached as a lazy
module-level singleton keyed on (model, device, compute_type). Importing
this module is cheap: ``faster_whisper`` is imported lazily inside the
singleton getter, so CPU-only images that do not ship the package can still
import the module (the import error only surfaces when a transcribe is
actually attempted).
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from app.core.config import Settings, get_settings
from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.utils.lang import detect_language

logger = structlog.get_logger(__name__)

# Whisper info.language codes that should be treated as "auto-detect" sentinels.
_AUTO_SENTINELS = {"", "auto", "unknown"}

# Conservative confidence when a segment exposes no avg_logprob.
_DEFAULT_CONFIDENCE = 0.8


class WhisperConfigError(RuntimeError):
    """Raised when faster-whisper STT is configured incorrectly or the
    package is missing. Caller decides whether to fail closed."""


# ----------------------------------------------------------------------------
# Lazy module-level model singleton
# ----------------------------------------------------------------------------
_model_cache: dict[tuple[str, str, str], Any] = {}
_model_lock = threading.Lock()


def _load_whisper_model_class():
    """Lazily import and return ``faster_whisper.WhisperModel``.

    Kept as a tiny indirection so tests can patch the constructor without a
    real model download, and so importing this module stays cheap.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover — env problem
        raise WhisperConfigError(
            "faster-whisper package not installed. "
            "Run `pip install -r requirements.txt`."
        ) from exc
    return WhisperModel


def _get_model(model: str, device: str, compute_type: str) -> Any:
    """Return a cached ``WhisperModel``, constructing it once per config key.

    Thread-safe (Celery may run the sync wrapper from a worker thread).

    ``model`` may be a faster-whisper alias ("large-v3"), a local CTranslate2
    directory, or any Hugging Face Whisper checkpoint (e.g.
    ``Oriserve/Whisper-Hindi2Hinglish-Apex``). HF checkpoints that are not
    already in CTranslate2 format get converted once and cached on disk.
    """
    key = (model, device, compute_type)
    cached = _model_cache.get(key)
    if cached is not None:
        return cached
    with _model_lock:
        cached = _model_cache.get(key)
        if cached is not None:
            return cached
        ctor = _load_whisper_model_class()
        logger.info(
            "whisper.model_load_start",
            model=model,
            device=device,
            compute_type=compute_type,
            note="first run downloads the model weights and can take several "
            "minutes — the call will sit in stt_running until done",
        )
        try:
            instance = ctor(model, device=device, compute_type=compute_type)
        except (RuntimeError, ValueError) as exc:
            # faster-whisper only loads CTranslate2 models. A HF repo id
            # (contains "/") in transformers format lands here — convert it.
            if "/" not in model:
                raise
            logger.info("whisper.ct2_convert_needed", model=model, reason=str(exc))
            converted = _convert_to_ct2(model, compute_type)
            instance = ctor(converted, device=device, compute_type=compute_type)
        logger.info("whisper.model_load_done", model=model)
        _model_cache[key] = instance
        return instance


def _ct2_cache_dir() -> Path:
    base = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(base) / "v2t-ct2"


def _convert_to_ct2(model_id: str, compute_type: str) -> str:
    """Convert a transformers Whisper checkpoint to CTranslate2, once.

    The converted model is cached under the HF cache volume so worker
    restarts don't re-convert.
    """
    target = _ct2_cache_dir() / model_id.replace("/", "__")
    if (target / "model.bin").exists():
        return str(target)
    try:
        from ctranslate2.converters import TransformersConverter
    except ImportError as exc:  # pragma: no cover — ships with faster-whisper
        raise WhisperConfigError(
            "ctranslate2 is required to convert HF Whisper checkpoints."
        ) from exc
    quantization = compute_type if compute_type in ("int8", "int8_float16", "int16", "float16", "float32") else "int8"
    logger.info("whisper.ct2_convert_start", model=model_id, quantization=quantization)
    target.parent.mkdir(parents=True, exist_ok=True)
    TransformersConverter(
        model_id,
        copy_files=["tokenizer.json", "preprocessor_config.json"],
        load_as_float16=quantization in ("int8_float16", "float16"),
    ).convert(str(target), quantization=quantization, force=True)
    logger.info("whisper.ct2_convert_done", model=model_id, target=str(target))
    return str(target)


def _reset_model_cache() -> None:
    """Test hook — drop any cached model instances."""
    with _model_lock:
        _model_cache.clear()


# ----------------------------------------------------------------------------
# WhisperTranscriber
# ----------------------------------------------------------------------------
class WhisperTranscriber:
    """Wraps faster-whisper with the SarvamTranscriber public interface.

    Typical usage::

        svc = WhisperTranscriber()
        utterances = await svc.transcribe_file(
            call_id=call.id,
            audio_path="/tmp/call.wav",
        )
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

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
        glues that in). faster-whisper handles long audio natively, so the
        whole file is transcribed in a single call (no chunking). The heavy
        decode + inference runs in a thread so the event loop stays
        responsive.
        """
        utterances = await asyncio.to_thread(self._transcribe_sync, call_id, audio_path)
        if self._settings.local_diarization and utterances:
            from app.services.stt.local_diarization import assign_speaker_ids, diarize

            turns = await asyncio.to_thread(diarize, audio_path, self._settings)
            utterances = assign_speaker_ids(utterances, turns)
        logger.info(
            "whisper.transcribe_done",
            n_utterances=len(utterances),
            diarized=any(u.speaker_id for u in utterances),
            audio_path=audio_path,
        )
        return utterances

    # ------------------------------------------------------------------ Internals
    def _transcribe_sync(self, call_id: UUID, audio_path: str) -> list[UtteranceSchema]:
        model = _get_model(
            self._settings.whisper_model,
            self._settings.whisper_device,
            self._settings.whisper_compute_type,
        )

        lang_setting = (self._settings.whisper_language or "").strip().lower()
        language = None if lang_setting in _AUTO_SENTINELS else self._settings.whisper_language

        segments, info = model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
        )

        info_lang = getattr(info, "language", None)

        utterances: list[UtteranceSchema] = []
        for seg in segments:
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            seg_language = (
                self._language_from_code(info_lang)
                if info_lang
                else detect_language(text)
            )
            if seg_language is Language.OTHER:
                # Whisper's detected language is outside our mapped set;
                # fall back to the script-based heuristic on the text.
                seg_language = detect_language(text)
            utterances.append(
                UtteranceSchema(
                    call_id=call_id,
                    speaker=Speaker.UNKNOWN,
                    start_ts=float(getattr(seg, "start", 0.0) or 0.0),
                    end_ts=float(getattr(seg, "end", 0.0) or 0.0),
                    text=text,
                    language=seg_language,
                    confidence=self._confidence_from(seg),
                )
            )
        return utterances

    # ----------------------------------------------------------------
    @staticmethod
    def _confidence_from(seg: Any) -> float:
        """Derive a [0,1] confidence from ``seg.avg_logprob`` if present.

        avg_logprob is a (negative) mean token log-probability. ``exp`` maps
        it back to a pseudo-probability in (0, 1]; clamp for safety. Falls
        back to a conservative default when the field is absent.
        """
        avg = getattr(seg, "avg_logprob", None)
        if avg is None:
            return _DEFAULT_CONFIDENCE
        try:
            import math

            conf = math.exp(float(avg))
        except (TypeError, ValueError, OverflowError):
            return _DEFAULT_CONFIDENCE
        return max(0.0, min(1.0, conf))

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


__all__ = [
    "WhisperTranscriber",
    "WhisperConfigError",
]
