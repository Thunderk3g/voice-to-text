"""One-pass per-call analyzer: lead + disposition + sentiment over the WHOLE
transcript (both speakers). Clones the LLMExtractor scaffolding (chat_json strict
schema, Pydantic validate-and-skip, grounding guard for PII)."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError

from app.models.schemas import CallAnalysis, CallAnalysisResult, Lead, UtteranceSchema
from app.prompts.call_analysis import (
    CALL_ANALYSIS_SYSTEM,
    CALL_ANALYSIS_USER_TEMPLATE,
    build_transcript_block,
)
from app.prompts.call_analysis_schema import CALL_ANALYSIS_SCHEMA
from app.services.llm.groq_client import GroqClient

logger = structlog.get_logger(__name__)

_CHAR_BUDGET = 14_000
_GROUNDED_STRING_FIELDS = ("full_name", "email", "policy_no")  # phone handled separately
_DIGITS = re.compile(r"\D")


class CallAnalyzer:
    def __init__(self, client: GroqClient) -> None:
        self._client = client

    async def analyze(
        self, call_id: UUID, utterances: list[UtteranceSchema]
    ) -> CallAnalysisResult:
        if not utterances:
            return CallAnalysisResult(
                call_id=call_id,
                analysis=CallAnalysis(),
                used_model=self._client.model,
                raw_response=None,
            )

        block = build_transcript_block(
            [{"speaker": u.speaker.value, "text": u.text, "start_ts": u.start_ts}
             for u in utterances]
        )[:_CHAR_BUDGET]
        user = CALL_ANALYSIS_USER_TEMPLATE.format(transcript=block)

        try:
            payload = await self._client.chat_json(
                system=CALL_ANALYSIS_SYSTEM,
                user=user,
                json_schema=CALL_ANALYSIS_SCHEMA,
                schema_name="call_analysis",
            )
        except Exception as exc:  # noqa: BLE001 — retries exhausted
            logger.error("analyzer.llm_failed", call_id=str(call_id), error=str(exc))
            return CallAnalysisResult(call_id=call_id, analysis=CallAnalysis(),
                                      used_model=self._client.model, raw_response=None)

        try:
            analysis = CallAnalysis.model_validate(payload)
        except ValidationError as exc:
            logger.warning("analyzer.invalid_payload", call_id=str(call_id),
                           error=exc.errors(include_url=False)[:3])
            analysis = CallAnalysis()

        analysis.lead = _ground_lead(analysis.lead, utterances)
        return CallAnalysisResult(
            call_id=call_id, analysis=analysis,
            used_model=self._client.model, raw_response=_safe(payload),
        )


def _ground_lead(lead: Lead, utterances: list[UtteranceSchema]) -> Lead:
    joined = " ".join(u.text for u in utterances).lower()
    joined_digits = _DIGITS.sub("", joined)
    grounded: list[str] = []

    for field in _GROUNDED_STRING_FIELDS:
        val = getattr(lead, field)
        if val and str(val).lower() in joined:
            grounded.append(field)
        elif val:
            setattr(lead, field, None)

    if lead.phone:
        pdigits = _DIGITS.sub("", str(lead.phone))
        if pdigits and pdigits in joined_digits:
            grounded.append("phone")
        else:
            lead.phone = None

    lead.grounded_fields = grounded
    return lead


def _safe(payload: dict[str, Any]) -> str:
    try:
        import orjson
        return orjson.dumps(payload).decode("utf-8")
    except Exception:  # noqa: BLE001
        return repr(payload)


__all__ = ["CallAnalyzer"]
