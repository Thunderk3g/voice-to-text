"""Unit tests for ``app.services.extraction.llm_extractor.LLMExtractor``.

The Ollama HTTP layer is mocked via ``respx`` so we exercise the real
``OllamaClient`` retry / JSON-parse path.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.services.extraction.llm_extractor import LLMExtractor
from app.services.llm.ollama_client import OllamaClient


def _utt(call_id, text: str, speaker: Speaker = Speaker.CUSTOMER) -> UtteranceSchema:
    return UtteranceSchema(
        id=uuid4(),
        call_id=call_id,
        speaker=speaker,
        start_ts=0.0,
        end_ts=1.0,
        text=text,
        language=Language.ENGLISH,
        confidence=0.9,
    )


def _openai_chat_payload(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": get_settings().llm_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_extractor_parses_fenced_json_and_stamps_call_id() -> None:
    """LLM returns JSON inside ``` fences (vLLM ignoring json_object mode)."""
    call_id = uuid4()
    utts = [
        _utt(call_id, "Hi, I want to know my policy premium amount."),
        _utt(call_id, "Sure, what's your policy number?", speaker=Speaker.AGENT),
    ]

    fenced = (
        "```json\n"
        + json.dumps(
            {
                "questions": [
                    {
                        "raw_text": "I want to know my policy premium amount.",
                        "normalized_text": "What is my policy premium amount?",
                        "english_gloss": "What is my premium?",
                        "question_type": "question",
                        "intent": "premium_payment",
                        "secondary_intents": [],
                        "language": "en",
                        "confidence": 0.9,
                    }
                ]
            }
        )
        + "\n```"
    )

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_openai_chat_payload(fenced))
        )
        client = OllamaClient()
        extractor = LLMExtractor(client)
        result = await extractor.extract(call_id, utts)
        await client.aclose()

    assert result.call_id == call_id
    assert len(result.questions) == 1
    q = result.questions[0]
    assert q.call_id == call_id  # stamped
    assert q.intent.value == "premium_payment"
    # Best-effort utterance match — raw_text is a substring of utt #0.
    assert q.utterance_id == utts[0].id


@pytest.mark.asyncio
async def test_extractor_skips_malformed_questions() -> None:
    """One invalid question dict should not poison the rest of the batch."""
    call_id = uuid4()
    utts = [_utt(call_id, "Tell me about claim status.")]

    payload = {
        "questions": [
            # Invalid — missing required normalized_text + language.
            {"raw_text": "garbage"},
            # Valid
            {
                "raw_text": "Tell me about claim status.",
                "normalized_text": "What is the status of my claim?",
                "english_gloss": "Claim status?",
                "question_type": "question",
                "intent": "claim_process",
                "secondary_intents": [],
                "language": "en",
                "confidence": 0.8,
            },
            # Invalid — not an object
            "not a dict",
        ]
    }

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200, json=_openai_chat_payload(json.dumps(payload))
            )
        )
        client = OllamaClient()
        extractor = LLMExtractor(client)
        result = await extractor.extract(call_id, utts)
        await client.aclose()

    assert len(result.questions) == 1
    assert result.questions[0].intent.value == "claim_process"
    assert result.questions[0].call_id == call_id


@pytest.mark.asyncio
async def test_extractor_empty_utterances_short_circuits() -> None:
    call_id = uuid4()
    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        route = router.post("/chat/completions")
        client = OllamaClient()
        extractor = LLMExtractor(client)
        result = await extractor.extract(call_id, [])
        await client.aclose()

    assert result.questions == []
    assert result.call_id == call_id
    assert route.called is False


@pytest.mark.asyncio
async def test_extractor_handles_missing_questions_key() -> None:
    """Payload missing the ``questions`` array — we should return empty, not crash."""
    call_id = uuid4()
    utts = [_utt(call_id, "Why was my claim rejected?")]

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_chat_payload(json.dumps({"foo": "bar"})),
            )
        )
        client = OllamaClient()
        extractor = LLMExtractor(client)
        result = await extractor.extract(call_id, utts)
        await client.aclose()

    assert result.questions == []
    assert result.call_id == call_id
