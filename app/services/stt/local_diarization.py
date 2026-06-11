"""
Local speaker diarization for the open-source STT path.

Two strategies, tried in order:

1. **Stereo channel split** — call-centre recorders often put the agent on
   one channel and the customer on the other. When the file is stereo and
   the channels are *not* duplicates of each other, each channel's
   non-silent ranges become speaker turns. Free and more accurate than any
   model.
2. **pyannote** ``speaker-diarization-community-1`` — gated Hugging Face
   model (set ``HF_TOKEN`` after accepting the terms once); weights are
   cached and run fully offline afterwards. CPU-friendly.

The output is a list of :class:`SpeakerTurn`; ``assign_speaker_ids`` then
stamps STT utterances with the ``speaker_id`` of the turn they overlap
most, after which ``map_speaker_roles`` decides who is AGENT vs CUSTOMER —
the same flow the Sarvam batch path uses.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import Settings, get_settings
from app.models.schemas import UtteranceSchema

logger = structlog.get_logger(__name__)

#: Channels whose per-window loudness never diverges more than this are
#: treated as duplicated mono (fake stereo) — channel split is pointless.
_DUPLICATE_DB_SPREAD = 3.0


@dataclass(frozen=True)
class SpeakerTurn:
    start: float  # seconds
    end: float
    speaker_id: str


# ----------------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------------
def diarize(audio_path: str, settings: Settings | None = None) -> list[SpeakerTurn]:
    """Return speaker turns for the file (stereo split, else pyannote)."""
    settings = settings or get_settings()
    turns = _stereo_split_turns(audio_path)
    if turns:
        logger.info("diarization.stereo_split", n_turns=len(turns), audio_path=audio_path)
        return turns
    turns = _pyannote_turns(audio_path, settings)
    logger.info("diarization.pyannote", n_turns=len(turns), audio_path=audio_path)
    return turns


def assign_speaker_ids(
    utterances: list[UtteranceSchema], turns: list[SpeakerTurn]
) -> list[UtteranceSchema]:
    """Stamp each utterance with the speaker_id of its best-overlapping turn."""
    if not turns:
        return utterances

    def _best(u: UtteranceSchema) -> str | None:
        best_id, best_ov = None, 0.0
        for t in turns:
            ov = min(u.end_ts, t.end) - max(u.start_ts, t.start)
            if ov > best_ov:
                best_id, best_ov = t.speaker_id, ov
        return best_id

    return [u.model_copy(update={"speaker_id": _best(u)}) for u in utterances]


# ----------------------------------------------------------------------------
# Strategy 1 — stereo channel split
# ----------------------------------------------------------------------------
def _stereo_split_turns(audio_path: str) -> list[SpeakerTurn]:
    """Speaker turns from a true two-channel recording; [] when not applicable."""
    from pydub import AudioSegment, silence

    audio = AudioSegment.from_file(audio_path)
    if audio.channels != 2:
        return []

    left, right = audio.split_to_mono()
    if _channels_are_duplicates(left, right):
        return []

    turns: list[SpeakerTurn] = []
    for sid, channel in (("0", left), ("1", right)):
        thresh = channel.dBFS - 16 if channel.dBFS != float("-inf") else -50.0
        ranges = silence.detect_nonsilent(
            channel, min_silence_len=400, silence_thresh=thresh
        )
        turns.extend(
            SpeakerTurn(start=a / 1000.0, end=b / 1000.0, speaker_id=sid)
            for a, b in ranges
        )
    turns.sort(key=lambda t: t.start)
    return turns


def _channels_are_duplicates(left: Any, right: Any) -> bool:
    """Compare 1 s loudness profiles; near-identical means fake stereo."""
    step = 1000
    n = min(len(left), len(right))
    spread = 0.0
    for off in range(0, n, step):
        l_db = left[off : off + step].dBFS
        r_db = right[off : off + step].dBFS
        if l_db == float("-inf") and r_db == float("-inf"):
            continue
        if l_db == float("-inf") or r_db == float("-inf"):
            return False  # one side silent while the other talks — real split
        spread = max(spread, abs(l_db - r_db))
        if spread > _DUPLICATE_DB_SPREAD:
            return False
    return True


# ----------------------------------------------------------------------------
# Strategy 2 — pyannote community-1
# ----------------------------------------------------------------------------
_PYANNOTE_MODEL = "pyannote/speaker-diarization-community-1"
_pipeline_lock = threading.Lock()
_pipeline: Any = None


def _get_pipeline(settings: Settings) -> Any:
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError(
                "pyannote.audio is not installed — required for LOCAL_DIARIZATION "
                "on mono recordings. Run `pip install -r requirements.txt`."
            ) from exc
        token = settings.hf_token.get_secret_value() or None
        logger.info("diarization.pyannote_load_start", model=_PYANNOTE_MODEL)
        _pipeline = Pipeline.from_pretrained(_PYANNOTE_MODEL, token=token)
        logger.info("diarization.pyannote_load_done", model=_PYANNOTE_MODEL)
        return _pipeline


def _pyannote_turns(audio_path: str, settings: Settings) -> list[SpeakerTurn]:
    pipeline = _get_pipeline(settings)
    annotation = pipeline(audio_path, num_speakers=settings.sarvam_num_speakers)
    # pyannote labels speakers "SPEAKER_00", "SPEAKER_01", ... — normalise to "0", "1".
    labels: dict[str, str] = {}
    turns: list[SpeakerTurn] = []
    for segment, _, label in annotation.itertracks(yield_label=True):
        sid = labels.setdefault(str(label), str(len(labels)))
        turns.append(SpeakerTurn(start=float(segment.start), end=float(segment.end), speaker_id=sid))
    turns.sort(key=lambda t: t.start)
    return turns


__all__ = ["SpeakerTurn", "diarize", "assign_speaker_ids"]
