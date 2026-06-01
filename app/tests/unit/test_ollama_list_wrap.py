"""
Unit tests: bare-list recovery in the Ollama client.

Local models (e.g. Gemma) sometimes return the inner array directly instead
of the ``{"questions": [...]}`` object the schema asks for. ``_sole_array_property``
lets ``chat_json`` recover by wrapping the list under the schema's single
array property — but only when that's unambiguous.
"""

from __future__ import annotations

from app.prompts.extraction_schema import EXTRACTION_RESPONSE_SCHEMA
from app.services.llm.ollama_client import _sole_array_property


def test_extraction_schema_sole_array_property_is_questions() -> None:
    assert _sole_array_property(EXTRACTION_RESPONSE_SCHEMA) == "questions"


def test_none_for_missing_or_non_dict_schema() -> None:
    assert _sole_array_property(None) is None
    assert _sole_array_property({}) is None


def test_none_when_multiple_properties() -> None:
    schema = {
        "type": "object",
        "properties": {
            "questions": {"type": "array"},
            "summary": {"type": "string"},
        },
    }
    assert _sole_array_property(schema) is None


def test_none_when_sole_property_not_array() -> None:
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    assert _sole_array_property(schema) is None
