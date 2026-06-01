"""
JSON-schema for the customer-question extraction LLM call.

This mirrors the shape consumed by ``LLMExtractor._coerce_questions`` and the
``ExtractedQuestion`` Pydantic model. The Intent / Language / QuestionType
enums are inlined as ``enum`` lists so providers that honor strict JSON-schema
mode (Groq, OpenAI, Ollama 0.5+) can reject malformed outputs before they
reach our Pydantic validator.

Keep this in sync with:
  * app/models/enums.py  (Intent, Language, QuestionType)
  * app/models/schemas.py::ExtractedQuestion
"""

from __future__ import annotations

from app.models.enums import Intent, Language, QuestionType


_INTENTS: list[str] = [m.value for m in Intent]
_LANGUAGES: list[str] = [m.value for m in Language]
_QTYPES: list[str] = [m.value for m in QuestionType]


EXTRACTION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # Strict JSON-schema mode (Groq/OpenAI) requires EVERY key in
                # ``properties`` to be listed here, else the API rejects the
                # request with HTTP 400. ``secondary_intents`` is semantically
                # optional but must still be required at the schema level; the
                # model emits ``[]`` when there are none (ExtractedQuestion
                # defaults it to an empty list).
                "required": [
                    "raw_text",
                    "normalized_text",
                    "english_gloss",
                    "question_type",
                    "intent",
                    "secondary_intents",
                    "language",
                    "confidence",
                ],
                "properties": {
                    "raw_text": {"type": "string"},
                    "normalized_text": {"type": "string"},
                    "english_gloss": {"type": "string"},
                    "question_type": {"type": "string", "enum": _QTYPES},
                    "intent": {"type": "string", "enum": _INTENTS},
                    "secondary_intents": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string", "enum": _INTENTS},
                    },
                    "language": {"type": "string", "enum": _LANGUAGES},
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
            },
        }
    },
}


__all__ = ["EXTRACTION_RESPONSE_SCHEMA"]
