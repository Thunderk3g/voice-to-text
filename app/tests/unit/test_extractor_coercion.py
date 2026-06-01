"""
Unit tests: tolerant coercion of LLM question payloads.

Local models don't always honor the strict schema — they use alias keys
(``query``/``question``/...) and omit ``language``. ``_coerce_questions`` must
recover usable questions instead of dropping them, while still rejecting items
with no usable text.
"""

from __future__ import annotations

from uuid import uuid4

from app.services.extraction.llm_extractor import _coerce_questions


def test_alias_query_becomes_raw_and_normalized_text() -> None:
    call_id = uuid4()
    payload = {"questions": [{"query": "Can I increase the coverage later?"}]}

    out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
    q = out[0]
    assert q.raw_text == "Can I increase the coverage later?"
    assert q.normalized_text == "Can I increase the coverage later?"
    # language was missing -> auto-detected to a valid enum value.
    assert q.language is not None


def test_missing_normalized_text_falls_back_to_raw_text() -> None:
    call_id = uuid4()
    payload = {"questions": [{"raw_text": "What is my policy status?", "language": "en"}]}

    out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
    assert out[0].normalized_text == "What is my policy status?"


def test_item_with_no_usable_text_is_dropped() -> None:
    call_id = uuid4()
    payload = {"questions": [{"context": "no question here"}]}

    out = _coerce_questions(payload, call_id, [])

    assert out == []


def test_wellformed_item_still_validates() -> None:
    call_id = uuid4()
    payload = {
        "questions": [
            {
                "raw_text": "claim kaise karein?",
                "normalized_text": "How do I file a claim?",
                "english_gloss": "How do I file a claim?",
                "question_type": "question",
                "intent": "claim_process",
                "secondary_intents": [],
                "language": "hi-en",
                "confidence": 0.9,
            }
        ]
    }

    out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
    assert out[0].intent.value == "claim_process"
    assert out[0].confidence == 0.9
