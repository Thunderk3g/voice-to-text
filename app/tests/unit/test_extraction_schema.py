"""
Unit tests: extraction response JSON-schema must satisfy strict-mode providers.

Groq/OpenAI strict ``response_format`` rejects (HTTP 400) any object schema
whose ``required`` array omits a key present in ``properties``. These tests
guard against that regression (the original bug: ``secondary_intents`` was in
``properties`` but missing from ``required``).
"""

from __future__ import annotations

from app.prompts.extraction_schema import EXTRACTION_RESPONSE_SCHEMA


def _assert_required_covers_properties(obj: dict) -> None:
    assert set(obj.get("required", [])) == set(obj["properties"].keys())


def test_top_level_required_covers_all_properties() -> None:
    _assert_required_covers_properties(EXTRACTION_RESPONSE_SCHEMA)


def test_questions_item_required_covers_all_properties() -> None:
    item = EXTRACTION_RESPONSE_SCHEMA["properties"]["questions"]["items"]
    _assert_required_covers_properties(item)


def test_secondary_intents_is_required() -> None:
    item = EXTRACTION_RESPONSE_SCHEMA["properties"]["questions"]["items"]
    assert "secondary_intents" in item["required"]
