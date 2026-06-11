"""
AI4Bharat IndicConformer STT — local, MIT-licensed, all 22 scheduled Indian
languages (https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual).

Trade-offs vs Whisper: native-script output (Hinglish comes out in
Devanagari) and CTC decoding with no timestamps. To still get usable
utterance timing and speakers, this provider leans on local diarization:
when ``LOCAL_DIARIZATION=true`` each speaker turn is sliced out of the file
and transcribed separately (turn boundaries provide the timestamps and
``speaker_id``); without diarization the whole file becomes one utterance.

The checkpoint is gated on Hugging Face — set ``HF_TOKEN`` after accepting
the model terms once; weights are cached locally afterwards.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import UUID

import structlog

from app.core.config import Settings, get_settings
from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.utils.lang import detect_language

logger = structlog.get_logger(__name__)

_SAMPLE_RATE = 16_000

#: Our Language enum → IndicConformer language code. The model needs an
#: explicit language; OTHER falls back to Hindi (the dominant traffic).
_LANG_CODE = {
    Language.HINDI: "hi",
    Language.ENGLISH: "en",
    Language.TAMIL: "ta",
    Language.TELUGU: "te",
}


class IndicConformerConfigError(RuntimeError):
    """Raised when the IndicConformer provider is misconfigured."""


_model_lock = threading.Lock()
_model: Any = None


def _get_model(settings: Settings) -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from transformers import AutoModel
        except ImportError as exc:  # pragma: no cover — env problem
            raise IndicConformerConfigError(
                "transformers is not installed. Run `pip install -r requirements.txt`."
            ) from exc
        token = settings.hf_token.get_secret_value() or None
        logger.info("indic_conformer.model_load_start", model=settings.indic_conformer_model)
        _model = AutoModel.from_pretrained(
            settings.indic_conformer_model, trust_remote_code=True, token=token
        )
        logger.info("indic_conformer.model_load_done", model=settings.indic_conformer_model)
        return _model


class IndicConformerTranscriber:
    """Duck-type compatible with the other providers:
    ``async transcribe_file(*, call_id, audio_path) -> list[UtteranceSchema]``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def transcribe_file(
        self, *, call_id: UUID, audio_path: str
    ) -> list[UtteranceSchema]:
        utterances = await asyncio.to_thread(self._transcribe_sync, call_id, audio_path)
        logger.info(
            "indic_conformer.transcribe_done",
            n_utterances=len(utterances),
            audio_path=audio_path,
        )
        return utterances

    # ------------------------------------------------------------------ Internals
    def _transcribe_sync(self, call_id: UUID, audio_path: str) -> list[UtteranceSchema]:
        try:
            import torchaudio
        except ImportError as exc:  # pragma: no cover — env problem
            raise IndicConformerConfigError(
                "torchaudio is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        wav, sr = torchaudio.load(audio_path)
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        if sr != _SAMPLE_RATE:
            wav = torchaudio.functional.resample(wav, sr, _SAMPLE_RATE)

        model = _get_model(self._settings)
        lang = self._lang_code()
        duration_s = wav.shape[1] / _SAMPLE_RATE

        if self._settings.local_diarization:
            from app.services.stt.local_diarization import diarize

            turns = diarize(audio_path, self._settings)
            if turns:
                return self._transcribe_turns(call_id, model, wav, lang, turns)

        text = str(model(wav, lang, "ctc")).strip()
        if not text:
            return []
        return [self._utterance(call_id, text, 0.0, duration_s, speaker_id=None)]

    def _transcribe_turns(
        self, call_id: UUID, model: Any, wav: Any, lang: str, turns: list
    ) -> list[UtteranceSchema]:
        out: list[UtteranceSchema] = []
        n_samples = wav.shape[1]
        for turn in turns:
            a = max(0, int(turn.start * _SAMPLE_RATE))
            b = min(n_samples, int(turn.end * _SAMPLE_RATE))
            if b - a < int(0.2 * _SAMPLE_RATE):  # sub-200ms slivers transcribe to noise
                continue
            text = str(model(wav[:, a:b], lang, "ctc")).strip()
            if not text:
                continue
            out.append(
                self._utterance(call_id, text, turn.start, turn.end, speaker_id=turn.speaker_id)
            )
        return out

    def _utterance(
        self, call_id: UUID, text: str, start: float, end: float, speaker_id: str | None
    ) -> UtteranceSchema:
        return UtteranceSchema(
            call_id=call_id,
            speaker=Speaker.UNKNOWN,
            speaker_id=speaker_id,
            start_ts=start,
            end_ts=end,
            text=text,
            language=detect_language(text),
            confidence=0.8,  # CTC exposes no usable confidence
        )

    def _lang_code(self) -> str:
        configured = (self._settings.whisper_language or "").strip().lower()
        if configured in {c for c in _LANG_CODE.values()}:
            return configured
        return "hi"


__all__ = ["IndicConformerTranscriber", "IndicConformerConfigError"]
