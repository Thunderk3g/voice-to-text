"""Unit tests for local diarization alignment (pure logic, no models)."""

from __future__ import annotations

from uuid import uuid4

from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.services.stt.local_diarization import SpeakerTurn, assign_speaker_ids

CALL_ID = uuid4()


def _utt(start: float, end: float, text: str = "x") -> UtteranceSchema:
    return UtteranceSchema(
        call_id=CALL_ID,
        speaker=Speaker.UNKNOWN,
        start_ts=start,
        end_ts=end,
        text=text,
        language=Language.HINDI,
        confidence=0.9,
    )


def test_utterances_get_best_overlapping_turn():
    turns = [
        SpeakerTurn(0.0, 5.0, "0"),
        SpeakerTurn(5.0, 9.0, "1"),
        SpeakerTurn(9.0, 14.0, "0"),
    ]
    utts = [_utt(0.5, 4.0), _utt(4.5, 8.5), _utt(9.2, 13.0)]
    out = assign_speaker_ids(utts, turns)
    assert [u.speaker_id for u in out] == ["0", "1", "0"]
    # input untouched
    assert all(u.speaker_id is None for u in utts)


def test_utterance_outside_all_turns_stays_unlabelled():
    out = assign_speaker_ids([_utt(20.0, 22.0)], [SpeakerTurn(0.0, 5.0, "0")])
    assert out[0].speaker_id is None


def test_no_turns_returns_input_unchanged():
    utts = [_utt(0.0, 1.0)]
    assert assign_speaker_ids(utts, []) is utts
