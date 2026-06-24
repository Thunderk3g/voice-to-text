"""Per-call analysis prompt: lead info + disposition + sentiment in ONE pass over
the WHOLE transcript (both speakers, unlike the customer-only question extractor)."""
from __future__ import annotations

from app.prompts.extraction import build_transcript_block  # reuse

CALL_ANALYSIS_SYSTEM = """\
You analyze a single Indian life-insurance INBOUND SERVICE call transcript with
speaker labels (AGENT / CUSTOMER). Read the ENTIRE conversation — both parties —
and return ONE JSON object describing the lead, the call disposition, and sentiment.

LEAD: extract any identifying / profile attributes that are actually stated in the
call (by either speaker). Use null for anything not clearly stated. Never invent.
Phone numbers, names, emails, policy numbers must be quoted VERBATIM as spoken
(we verify them against the transcript). For `phone`, output the raw digits as
spoken; downstream normalizes to a 10-digit mobile.

DISPOSITION (pick exactly one): resolved, info_provided, callback_requested,
follow_up_payment, complaint, escalation, not_interested, not_eligible,
service_request, wrong_number, dnd, no_response, other.
Give a one-line `disposition_rationale` grounded in the call.

SENTIMENT: overall customer sentiment — positive, neutral, or negative.
`escalation` = true if the customer demanded a supervisor / threatened to leave /
raised a serious grievance.

Output STRICT JSON only. No prose, no markdown fences.
"""

CALL_ANALYSIS_USER_TEMPLATE = """\
Transcript (speaker-labeled):

{transcript}

Return one JSON object with keys: lead, disposition, disposition_confidence,
disposition_rationale, sentiment, sentiment_confidence, escalation.
"""

__all__ = ["CALL_ANALYSIS_SYSTEM", "CALL_ANALYSIS_USER_TEMPLATE", "build_transcript_block"]
