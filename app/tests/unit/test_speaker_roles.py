"""Unit tests for diarization-aware speaker role mapping."""

from __future__ import annotations

from uuid import uuid4

from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.services.stt.speaker_heuristic import map_speaker_roles

CALL_ID = uuid4()


def _utt(text: str, speaker_id: str | None, start: float) -> UtteranceSchema:
    return UtteranceSchema(
        call_id=CALL_ID,
        speaker=Speaker.UNKNOWN,
        speaker_id=speaker_id,
        start_ts=start,
        end_ts=start + 2.0,
        text=text,
        language=Language.HINGLISH,
        confidence=0.9,
    )


def test_greeting_speaker_becomes_agent():
    utts = [
        _utt("Good morning, thank you for calling Bajaj Allianz, how may I help you", "0", 0.0),
        _utt("haan mujhe policy details chahiye, premium kya hai?", "1", 5.0),
        _utt("Sure ma'am, your policy number please", "0", 9.0),
        _utt("kab tak milega claim?", "1", 13.0),
    ]
    out = map_speaker_roles(utts)
    assert [u.speaker for u in out] == [
        Speaker.AGENT, Speaker.CUSTOMER, Speaker.AGENT, Speaker.CUSTOMER,
    ]
    # original list untouched
    assert all(u.speaker is Speaker.UNKNOWN for u in utts)


def test_question_heavy_terse_speaker_is_customer_without_greeting():
    utts = [
        _utt("kya hai ye policy? kaise claim karu? kab milega?", "1", 0.0),
        _utt(
            "Sir aapki policy ke andar accidental cover included hai aur premium "
            "annual basis pe dena hota hai jisme aapko tax benefit bhi milta hai "
            "under section 80C of the income tax act",
            "0",
            4.0,
        ),
    ]
    out = map_speaker_roles(utts)
    by_id = {u.speaker_id: u.speaker for u in out}
    assert by_id["0"] is Speaker.AGENT
    assert by_id["1"] is Speaker.CUSTOMER


def test_single_speaker_id_labelled_agent():
    utts = [_utt("hello hello", "0", 0.0), _utt("test test", "0", 3.0)]
    out = map_speaker_roles(utts)
    assert all(u.speaker is Speaker.AGENT for u in out)


def test_no_speaker_ids_falls_back_to_segment_heuristic():
    utts = [
        _utt("Good morning, thank you for calling Bajaj Allianz", None, 0.0),
        _utt("mujhe claim status check karna hai", None, 4.0),
    ]
    out = map_speaker_roles(utts)
    assert out[0].speaker is Speaker.AGENT
    assert all(u.speaker is not Speaker.UNKNOWN for u in out)


def test_empty_input():
    assert map_speaker_roles([]) == []
