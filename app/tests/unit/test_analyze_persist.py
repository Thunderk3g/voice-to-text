# app/tests/unit/test_analyze_persist.py
from uuid import uuid4
from app.models.schemas import CallAnalysis, CallAnalysisResult, Lead
from app.models.enums import CallDisposition, SentimentLabel
from app.workers.tasks import _call_analysis_metadata


def test_metadata_shape():
    res = CallAnalysisResult(
        call_id=uuid4(),
        analysis=CallAnalysis(
            lead=Lead(phone="9876543210", full_name="A B", grounded_fields=["phone"]),
            disposition=CallDisposition.RESOLVED, disposition_confidence=0.9,
            sentiment=SentimentLabel.POSITIVE, sentiment_confidence=0.8, escalation=False,
        ),
        used_model="m",
    )
    meta = _call_analysis_metadata(res)
    assert meta["disposition"] == "resolved"
    assert meta["sentiment"] == "positive"
    assert meta["lead"]["phone"] == "9876543210"
    assert meta["model"] == "m"


from app.workers.tasks import _first_analysis_stage


def test_first_analysis_stage_modes():
    assert _first_analysis_stage("lead") == "v2t.analyze"
    assert _first_analysis_stage("both") == "v2t.analyze"
    assert _first_analysis_stage("faq") == "v2t.extract"
