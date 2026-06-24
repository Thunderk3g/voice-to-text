"""Strict JSON schema for the per-call analysis LLM call. Mirrors CallAnalysis."""
from __future__ import annotations

from app.models.enums import CallDisposition, SentimentLabel

_DISPOSITIONS = [d.value for d in CallDisposition]
_SENTIMENTS = [s.value for s in SentimentLabel]

_LEAD_PROPS = {
    "full_name": {"type": ["string", "null"]},
    "phone": {"type": ["string", "null"]},
    "email": {"type": ["string", "null"]},
    "age": {"type": ["integer", "null"]},
    "gender": {"type": ["string", "null"]},
    "occupation": {"type": ["string", "null"]},
    "education": {"type": ["string", "null"]},
    "income_band": {"type": ["string", "null"]},
    "pincode": {"type": ["string", "null"]},
    "product_interest": {"type": ["string", "null"]},
    "policy_no": {"type": ["string", "null"]},
    "callback_time": {"type": ["string", "null"]},
}

CALL_ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lead", "disposition", "disposition_confidence", "sentiment",
                 "sentiment_confidence", "escalation"],
    "properties": {
        "lead": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_LEAD_PROPS.keys()),
            "properties": _LEAD_PROPS,
        },
        "disposition": {"type": "string", "enum": _DISPOSITIONS},
        "disposition_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "disposition_rationale": {"type": ["string", "null"]},
        "sentiment": {"type": "string", "enum": _SENTIMENTS},
        "sentiment_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "escalation": {"type": "boolean"},
    },
}

# disposition_rationale is optional content-wise but strict mode needs it required:
CALL_ANALYSIS_SCHEMA["required"].append("disposition_rationale")

__all__ = ["CALL_ANALYSIS_SCHEMA"]
