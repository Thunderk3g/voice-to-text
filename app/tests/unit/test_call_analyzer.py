from __future__ import annotations
import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.models.enums import Language, Speaker, CallDisposition, SentimentLabel
from app.models.schemas import UtteranceSchema
from app.services.extraction.call_analysis import CallAnalyzer
from app.services.llm.groq_client import GroqClient


def _utt(call_id, text, speaker=Speaker.CUSTOMER):
    return UtteranceSchema(id=uuid4(), call_id=call_id, speaker=speaker,
                           start_ts=0.0, end_ts=1.0, text=text,
                           language=Language.ENGLISH, confidence=0.9)


def _payload(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0,
            "model": get_settings().llm_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.mark.asyncio
async def test_analyzer_extracts_grounded_lead_and_disposition():
    call_id = uuid4()
    utts = [
        _utt(call_id, "Hello, my name is Rahul Sharma and my number is 9876543210."),
        _utt(call_id, "Thank you Rahul, how can I help?", speaker=Speaker.AGENT),
        _utt(call_id, "I want to know my policy maturity date."),
    ]
    body = json.dumps({
        "lead": {"full_name": "Rahul Sharma", "phone": "9876543210", "email": None,
                 "age": None, "gender": None, "occupation": None, "education": None,
                 "income_band": None, "pincode": None, "product_interest": "maturity",
                 "policy_no": None, "callback_time": None},
        "disposition": "info_provided", "disposition_confidence": 0.8,
        "disposition_rationale": "Agent answered the maturity query.",
        "sentiment": "neutral", "sentiment_confidence": 0.7, "escalation": False,
    })
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        r.post("/chat/completions").mock(return_value=httpx.Response(200, json=_payload(body)))
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, utts)
        await client.aclose()

    assert res.call_id == call_id
    assert res.analysis.disposition == CallDisposition.INFO_PROVIDED
    assert res.analysis.sentiment == SentimentLabel.NEUTRAL
    assert res.analysis.lead.full_name == "Rahul Sharma"     # grounded
    assert res.analysis.lead.phone == "9876543210"           # grounded (digits present)
    assert "phone" in res.analysis.lead.grounded_fields


@pytest.mark.asyncio
async def test_analyzer_nulls_ungrounded_phone():
    call_id = uuid4()
    utts = [_utt(call_id, "I just want my premium receipt please.")]
    body = json.dumps({
        "lead": {"full_name": None, "phone": "9999999999", "email": None, "age": None,
                 "gender": None, "occupation": None, "education": None, "income_band": None,
                 "pincode": None, "product_interest": None, "policy_no": None, "callback_time": None},
        "disposition": "service_request", "disposition_confidence": 0.6,
        "disposition_rationale": "Asked for receipt.",
        "sentiment": "neutral", "sentiment_confidence": 0.5, "escalation": False,
    })
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        r.post("/chat/completions").mock(return_value=httpx.Response(200, json=_payload(body)))
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, utts)
        await client.aclose()

    assert res.analysis.lead.phone is None        # 9999999999 not in transcript -> nulled
    assert "phone" not in res.analysis.lead.grounded_fields


@pytest.mark.asyncio
async def test_analyzer_empty_utterances_short_circuits():
    call_id = uuid4()
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        route = r.post("/chat/completions")
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, [])
        await client.aclose()
    assert route.called is False
    assert res.call_id == call_id
