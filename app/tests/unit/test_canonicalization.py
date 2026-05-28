"""Unit tests for ``app.services.canonicalization.faq.FAQCanonicalizer``.

We mock the vLLM HTTP layer with ``respx`` and inject a fake
``get_cluster_examples_async`` callable.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.models.enums import Intent, Language
from app.services.canonicalization.faq import (
    ClusterContext,
    ClusterExample,
    FAQCanonicalizer,
)
from app.services.llm.ollama_client import OllamaClient


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


def _ctx(cluster_id, n_examples: int = 20) -> ClusterContext:
    centroid = [1.0, 0.0, 0.0]
    examples = []
    # First example is closest to the centroid (perfect cosine 1.0).
    examples.append(
        ClusterExample(
            question_id=uuid4(),
            text="My policy premium amount?",
            embedding=[1.0, 0.0, 0.0],
        )
    )
    # The rest progressively further from the centroid.
    for i in range(1, n_examples):
        examples.append(
            ClusterExample(
                question_id=uuid4(),
                text=f"random off-axis question {i}",
                embedding=[0.1, 1.0, float(i) * 0.01],
            )
        )
    return ClusterContext(
        cluster_id=cluster_id,
        centroid=centroid,
        dominant_language=Language.ENGLISH,
        dominant_intents=[Intent.PREMIUM_PAYMENT],
        total_members=n_examples,
        examples=examples,
    )


@pytest.mark.asyncio
async def test_canonicalize_happy_path() -> None:
    cluster_id = uuid4()
    ctx = _ctx(cluster_id)

    captured: dict[str, str] = {}

    async def get_examples(cid):
        assert cid == cluster_id
        return ctx

    def _route_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["user"] = body["messages"][1]["content"]
        return httpx.Response(
            200,
            json=_openai_chat_payload(
                json.dumps(
                    {
                        "canonical_question": "What is my policy premium amount?",
                        "canonical_question_en": "What is my policy premium amount?",
                        "suggested_answer": "Your premium is shown on your policy schedule.",
                        "confidence": 0.91,
                    }
                )
            ),
        )

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(side_effect=_route_handler)
        client = OllamaClient()
        canon = FAQCanonicalizer(client, get_examples)
        faq = await canon.canonicalize(cluster_id)
        await client.aclose()

    assert faq.cluster_id == cluster_id
    assert faq.canonical_question.startswith("What is my policy premium")
    assert faq.confidence == pytest.approx(0.91)
    assert faq.language == Language.ENGLISH
    assert faq.version == 1
    # The central example should appear in the prompt; off-axis ones may not.
    assert "My policy premium amount?" in captured["user"]


@pytest.mark.asyncio
async def test_canonicalize_caps_examples_at_twelve() -> None:
    cluster_id = uuid4()
    ctx = _ctx(cluster_id, n_examples=25)

    async def get_examples(cid):
        return ctx

    captured_user: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured_user.append(body["messages"][1]["content"])
        return httpx.Response(
            200,
            json=_openai_chat_payload(
                json.dumps(
                    {
                        "canonical_question": "Q",
                        "canonical_question_en": "Q",
                        "suggested_answer": "",
                        "confidence": 0.5,
                    }
                )
            ),
        )

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(side_effect=_handler)
        client = OllamaClient()
        canon = FAQCanonicalizer(client, get_examples)
        faq = await canon.canonicalize(cluster_id)
        await client.aclose()

    # The prompt rendered with "({n} shown of {total})" — verify n == 12.
    assert "12 shown of 25" in captured_user[0]
    assert faq.suggested_answer is None  # empty string normalized to None
    assert faq.confidence == 0.5


@pytest.mark.asyncio
async def test_canonicalize_falls_back_to_top_example_when_llm_empty() -> None:
    cluster_id = uuid4()
    ctx = _ctx(cluster_id)

    async def get_examples(cid):
        return ctx

    settings = get_settings()
    base = settings.llm_base_url.rstrip("/")
    with respx.mock(base_url=base, assert_all_called=False) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=_openai_chat_payload(
                    json.dumps(
                        {
                            "canonical_question": "",
                            "suggested_answer": "",
                            "confidence": "not_a_number",
                        }
                    )
                ),
            )
        )
        client = OllamaClient()
        canon = FAQCanonicalizer(client, get_examples)
        faq = await canon.canonicalize(cluster_id)
        await client.aclose()

    # Fallback uses the most-central example.
    assert faq.canonical_question == "My policy premium amount?"
    assert faq.confidence == 0.0


@pytest.mark.asyncio
async def test_canonicalize_raises_on_empty_cluster() -> None:
    cluster_id = uuid4()

    async def get_examples(cid):
        return ClusterContext(
            cluster_id=cluster_id,
            centroid=[1.0, 0.0],
            dominant_language=Language.ENGLISH,
            dominant_intents=[Intent.OTHER],
            total_members=0,
            examples=[],
        )

    client = OllamaClient()
    canon = FAQCanonicalizer(client, get_examples)
    with pytest.raises(ValueError):
        await canon.canonicalize(cluster_id)
    await client.aclose()
