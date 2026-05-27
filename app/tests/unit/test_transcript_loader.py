"""Unit tests for ``app.services.stt.transcript_loader.load_transcript``."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.models.enums import Language, Speaker
from app.services.stt.transcript_loader import load_transcript


def test_load_transcript_roundtrip(tmp_path):
    call_id = uuid4()
    payload = [
        {
            "speaker": "AGENT",
            "start_ts": 0.0,
            "end_ts": 3.2,
            "text": "Good morning, thank you for calling Bajaj Allianz.",
            "language": "en",
        },
        {
            "speaker": "CUSTOMER",
            "start_ts": 3.2,
            "end_ts": 6.5,
            "text": "Mera policy number kya hai?",
            # language intentionally omitted — should be detected.
        },
        {
            "speaker": "SPEAKER_42",  # unknown label → collapses to UNKNOWN
            "start_ts": 6.5,
            "end_ts": 8.0,
            "text": "Ek minute hold kijiye.",
            "language": "hi-en",
        },
    ]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    out = load_transcript(call_id, str(p))

    assert len(out) == 3
    assert all(u.call_id == call_id for u in out)
    assert out[0].speaker == Speaker.AGENT
    assert out[0].language == Language.ENGLISH
    assert out[0].text.startswith("Good morning")
    # Inferred language for the second row — Hinglish-ish text via detect_language.
    assert out[1].speaker == Speaker.CUSTOMER
    assert out[1].language in {Language.HINGLISH, Language.ROMAN_HINDI, Language.ENGLISH}
    assert out[2].speaker == Speaker.UNKNOWN
    assert out[2].language == Language.HINGLISH


def test_load_transcript_skips_malformed_rows(tmp_path):
    call_id = uuid4()
    payload = [
        {"speaker": "AGENT", "start_ts": 0.0, "end_ts": 1.0, "text": "hi"},
        {"speaker": "AGENT", "start_ts": 1.0, "end_ts": 2.0, "text": ""},  # empty -> skip
        "not a dict",  # skip
        {"speaker": "CUSTOMER", "start_ts": "bad", "end_ts": 3.0, "text": "Kya hua?"},  # bad float -> coerced to 0.0
    ]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    out = load_transcript(call_id, str(p))
    assert len(out) == 2
    assert out[1].start_ts == 0.0  # bad float coerced
    assert out[1].speaker == Speaker.CUSTOMER


def test_load_transcript_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_transcript(uuid4(), str(tmp_path / "nope.json"))


def test_load_transcript_non_list_raises(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"speaker": "AGENT"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_transcript(uuid4(), str(p))
