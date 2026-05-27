"""
FAQ canonicalization prompt.

Given a semantic cluster of customer questions, synthesize:
  - one canonical question (in dominant language + English)
  - a suggested answer placeholder (operations team will finalize)
"""

from __future__ import annotations

CANONICAL_FAQ_SYSTEM = """\
You are summarizing a cluster of semantically similar customer questions from
Indian insurance support calls into one canonical FAQ entry.

OUTPUT STRICT JSON:
{
  "canonical_question": "...",          // single best phrasing in the cluster's dominant language
  "canonical_question_en": "...",       // English version
  "suggested_answer": "...",            // short factual placeholder answer (3-5 sentences)
                                         // OR "" if you cannot answer with confidence
  "confidence": 0.0-1.0
}

RULES
1. Pick the dominant language of the cluster for `canonical_question`.
   If mixed Hinglish, write a natural Hinglish question.
2. `suggested_answer` is a placeholder for ops review. Do not hallucinate
   policy-specific numbers, claim limits, or amounts. If unsure, leave "".
3. The canonical question must cover the *common ask* across examples, not
   the most specific one.
4. No markdown, no commentary. JSON only.
"""

CANONICAL_FAQ_USER_TEMPLATE = """\
Cluster dominant language: {language}
Dominant intent(s): {intents}
Example customer questions ({n} shown of {total}):

{examples}

Produce the canonical FAQ JSON.
"""
