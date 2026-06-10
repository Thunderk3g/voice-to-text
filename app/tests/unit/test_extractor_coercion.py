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


def test_missing_intent_and_confidence_logs_degraded() -> None:
    """Items missing intent/confidence/english_gloss are KEPT but logged."""
    from structlog.testing import capture_logs

    call_id = uuid4()
    payload = {
        "questions": [
            {"raw_text": "What is the claim process?", "language": "en"}
        ]
    }

    with capture_logs() as cap_logs:
        out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1  # still kept
    assert out[0].intent.value == "other"  # pydantic default applied
    assert any(
        log["event"] == "extractor.fields_defaulted" for log in cap_logs
    )


def test_complete_item_does_not_log_degraded() -> None:
    from structlog.testing import capture_logs

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

    with capture_logs() as cap_logs:
        out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
    assert not any(
        log["event"] == "extractor.fields_defaulted" for log in cap_logs
    )


def _utterance(text: str):
    """Minimal UtteranceSchema for grounding tests."""
    from uuid import uuid4 as _uuid4

    from app.models.enums import Speaker
    from app.models.schemas import UtteranceSchema

    return UtteranceSchema(
        id=_uuid4(),
        call_id=_uuid4(),
        speaker=Speaker.CUSTOMER,
        start_ts=0.0,
        end_ts=1.0,
        text=text,
        language="en",
        confidence=1.0,
    )


def test_ungrounded_question_dropped_when_chunk_present() -> None:
    call_id = uuid4()
    chunk = [_utterance("Hello, I am calling about my policy renewal date.")]
    payload = {
        "questions": [
            {"raw_text": "What are the coverage options for critical illness?",
             "language": "en"}
        ]
    }

    out = _coerce_questions(payload, call_id, chunk)

    assert out == []  # hallucinated: matches no utterance


def test_grounded_question_kept() -> None:
    call_id = uuid4()
    chunk = [_utterance("Sir, what is the claim process for this policy?")]
    payload = {
        "questions": [
            {"raw_text": "what is the claim process", "language": "en"}
        ]
    }

    out = _coerce_questions(payload, call_id, chunk)

    assert len(out) == 1
    assert out[0].utterance_id == chunk[0].id


def test_empty_chunk_cannot_ground_so_question_is_kept() -> None:
    """Callers that pass no chunk (tests, future batch paths) keep questions."""
    call_id = uuid4()
    payload = {"questions": [{"raw_text": "Is this policy tax-deductible?",
                              "language": "en"}]}

    out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
