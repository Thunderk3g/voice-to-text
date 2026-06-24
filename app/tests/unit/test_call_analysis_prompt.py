# app/tests/unit/test_call_analysis_prompt.py
from app.prompts.call_analysis_schema import CALL_ANALYSIS_SCHEMA
from app.prompts.call_analysis import CALL_ANALYSIS_SYSTEM, CALL_ANALYSIS_USER_TEMPLATE
from app.models.enums import CallDisposition, SentimentLabel


def test_schema_is_strict_object_with_required_top_level():
    props = CALL_ANALYSIS_SCHEMA["properties"]
    assert set(["lead", "disposition", "sentiment", "escalation"]) <= set(props)
    assert CALL_ANALYSIS_SCHEMA["additionalProperties"] is False

def test_disposition_enum_matches_enum():
    assert set(CALL_ANALYSIS_SCHEMA["properties"]["disposition"]["enum"]) == {d.value for d in CallDisposition}

def test_sentiment_enum_matches_enum():
    assert set(CALL_ANALYSIS_SCHEMA["properties"]["sentiment"]["enum"]) == {s.value for s in SentimentLabel}

def test_user_template_has_transcript_placeholder():
    assert "{transcript}" in CALL_ANALYSIS_USER_TEMPLATE
    assert "CUSTOMER" in CALL_ANALYSIS_SYSTEM or "customer" in CALL_ANALYSIS_SYSTEM
