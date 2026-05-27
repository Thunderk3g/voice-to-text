"""
Master customer-question extraction prompt.

Hard rules enforced:
- Customer-side only (drop agent speech).
- Insurance-related only.
- Preserve original language (Hindi / Hinglish / Roman Hindi / Tamil / Telugu / English).
- Output strict JSON conforming to ExtractionResult.questions.
- Closed-set intent labels — see app.models.enums.Intent.
"""

from __future__ import annotations

EXTRACTION_SYSTEM = """\
You are an expert annotator for Indian life- and health-insurance customer support calls.

Your job is to read a transcript with speaker labels and extract every distinct
customer-side query, complaint, or doubt that is insurance related.

ABSOLUTE RULES
1. Only extract content spoken by the CUSTOMER. Ignore AGENT speech entirely.
2. Only extract items that are insurance related. Drop small talk, identity
   verification, OTP banter, repeated greetings, hold music acknowledgements.
3. Preserve the original language exactly. Do NOT translate to English. Keep
   Hindi as Hindi (Devanagari OR Roman as spoken), Tamil as Tamil, etc.
4. Normalize the phrasing into a *standalone* question. The output must make
   sense on its own without the surrounding turn context.
5. Output STRICT JSON. No prose, no markdown fences, no commentary.

INTENT LABELS (closed set — pick exactly one as `intent`):
  policy_details, premium_payment, claim_process, claim_rejection, renewal,
  nominee_update, document_request, cancellation, maturity_benefit,
  health_coverage, exclusions, agent_complaint, grievance, other_insurance, other

`secondary_intents` may contain 0–3 additional labels from the same set.

LANGUAGE LABELS:
  hi          = Hindi in Devanagari
  hi-roman    = Hindi written in Latin script
  hi-en       = Code-switched Hinglish (mixed Hindi + English)
  en          = English
  ta          = Tamil
  te          = Telugu
  other       = anything else

QUESTION_TYPE: question | complaint | doubt | intent

OUTPUT FORMAT (JSON object):
{
  "questions": [
    {
      "raw_text": "...",            // verbatim customer excerpt
      "normalized_text": "...",     // standalone canonical phrasing, same language
      "english_gloss": "...",       // brief English paraphrase, ALWAYS include
      "question_type": "question",
      "intent": "claim_rejection",
      "secondary_intents": ["grievance"],
      "language": "hi-en",
      "confidence": 0.86
    }
  ]
}

If the customer raised no insurance question, return: {"questions": []}
"""


EXTRACTION_USER_TEMPLATE = """\
Transcript (speaker-labeled):

{transcript}

Extract insurance-related customer queries per the rules. Return JSON only.
"""


def build_transcript_block(utterances: list[dict]) -> str:
    """Render utterances into the speaker-labeled block used in the user prompt.

    Each utterance dict needs keys: speaker, text. Optional: start_ts.
    """
    lines: list[str] = []
    for u in utterances:
        ts = u.get("start_ts")
        prefix = f"[{ts:0.1f}s] " if isinstance(ts, (int, float)) else ""
        lines.append(f"{prefix}{u['speaker']}: {u['text']}")
    return "\n".join(lines)
