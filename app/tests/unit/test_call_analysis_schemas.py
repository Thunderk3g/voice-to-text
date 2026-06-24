# app/tests/unit/test_call_analysis_schemas.py
from uuid import uuid4
from app.models.schemas import Lead, CallAnalysis, CallAnalysisResult
from app.models.enums import CallDisposition, SentimentLabel


def test_lead_all_optional():
    lead = Lead()
    assert lead.phone is None and lead.full_name is None

def test_call_analysis_minimal():
    a = CallAnalysis(
        disposition=CallDisposition.RESOLVED,
        sentiment=SentimentLabel.NEUTRAL,
    )
    assert a.escalation is False
    assert a.lead.phone is None

def test_result_wraps_analysis():
    cid = uuid4()
    res = CallAnalysisResult(
        call_id=cid,
        analysis=CallAnalysis(disposition=CallDisposition.OTHER, sentiment=SentimentLabel.NEUTRAL),
        used_model="openai/gpt-oss-120b",
    )
    assert res.call_id == cid
