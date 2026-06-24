# app/tests/unit/test_call_enums.py
from app.models.enums import CallDisposition, SentimentLabel, CallStatus


def test_call_disposition_values():
    vals = {d.value for d in CallDisposition}
    assert {"resolved", "callback_requested", "not_interested", "wrong_number",
            "dnd", "no_response", "other"} <= vals

def test_sentiment_values():
    assert {s.value for s in SentimentLabel} == {"positive", "neutral", "negative"}

def test_new_call_statuses_exist():
    assert CallStatus.ANALYSIS_RUNNING.value == "analysis_running"
    assert CallStatus.ANALYSIS_DONE.value == "analysis_done"
