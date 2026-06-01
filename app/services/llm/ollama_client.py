"""
Async Ollama (OpenAI-compatible) chat client used by every LLM-driven service.

Ollama exposes the OpenAI chat-completions API at ``${LLM_BASE_URL}/chat/completions``
(default ``http://ollama:11434/v1``). We wrap ``openai.AsyncOpenAI`` so that:

  * All callers go through a single retry / timeout surface.
  * JSON-mode (``response_format={"type": "json_object"}``) is requested.
    Ollama accepts this on its OpenAI-compat endpoint and translates it to
    its native ``format: "json"`` constraint. We *also* defensively run the
    raw content back through ``parse_llm_json`` — some Ollama builds wrap
    the payload in fences or trail prose.
  * Transient HTTP / network errors are retried with exponential backoff
    via ``tenacity``.

Public API:

  * ``OllamaClient.chat_json(system, user, ...)`` -> dict
  * ``OllamaClient.chat_text(system, user, ...)`` -> str
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from openai import APIConnectionError, APIError, APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings, get_settings
from app.utils.json_safe import LLMJsonError, parse_llm_json

logger = structlog.get_logger(__name__)


def _sole_array_property(json_schema: dict[str, Any] | None) -> str | None:
    """Return the name of the schema's single top-level array property.

    Used to recover from local models that return the inner array directly
    instead of the ``{"<key>": [...]}`` object the schema asks for. Returns
    ``None`` unless the schema is an object with exactly one property whose
    type is ``array`` (so we never guess for multi-field schemas).
    """
    if not isinstance(json_schema, dict):
        return None
    props = json_schema.get("properties")
    if not isinstance(props, dict) or len(props) != 1:
        return None
    key, spec = next(iter(props.items()))
    if isinstance(spec, dict) and spec.get("type") == "array":
        return key
    return None


# Exception families we treat as transient and retry on.
_RETRY_EXC: tuple[type[BaseException], ...] = (
    httpx.HTTPStatusError,
    httpx.TimeoutException,
    httpx.TransportError,
    APIConnectionError,
    APITimeoutError,
    APIError,
    RateLimitError,
    LLMJsonError,
)


class OllamaClient:
    """Thin async wrapper around ``AsyncOpenAI`` pointing at the Ollama server."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._settings: Settings = settings or get_settings()
        if self._settings.llm_insecure_tls:
            logger.warning(
                "llm_client_tls_verify_disabled",
                base_url=self._settings.llm_base_url,
            )
        timeout = httpx.Timeout(self._settings.llm_request_timeout_s)
        http_client = httpx.AsyncClient(
            verify=not self._settings.llm_insecure_tls,
            timeout=timeout,
        )
        self._client: AsyncOpenAI = client or AsyncOpenAI(
            base_url=self._settings.llm_base_url,
            api_key=self._settings.llm_api_key.get_secret_value(),
            timeout=timeout,
            max_retries=0,  # tenacity owns retries
            http_client=http_client,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def model(self) -> str:
        return self._settings.llm_model

    async def chat_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> dict[str, Any]:
        """Run a chat completion in JSON mode and return a parsed dict.

        When ``json_schema`` is provided we ask the model for strict
        JSON-schema-validated output (supported by Groq's OpenAI-compatible
        endpoint and by Ollama 0.5+). Without a schema we fall back to the
        looser ``json_object`` mode, which most providers tolerate.

        Retries transient errors *and* JSON-parse failures so that a model
        glitch (fences, trailing prose) gets a second chance on a fresh
        sample.
        """

        if json_schema is not None:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        else:
            response_format = {"type": "json_object"}

        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(4),
            retry=retry_if_exception_type(_RETRY_EXC),
            reraise=True,
        ):
            with attempt:
                content = await self._chat(
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    extra=extra,
                )
                try:
                    parsed = parse_llm_json(content)
                except LLMJsonError:
                    logger.warning(
                        "ollama.json_parse_failed",
                        attempt=attempt.retry_state.attempt_number,
                        snippet=content[:200],
                    )
                    raise

                if isinstance(parsed, list):
                    # Some local models (e.g. Gemma via Ollama) ignore the
                    # object wrapper and return the inner array directly. If the
                    # schema declares a single top-level array property, wrap the
                    # list under that key so callers get the expected object.
                    wrap_key = _sole_array_property(json_schema)
                    if wrap_key is not None:
                        logger.info(
                            "ollama.wrapped_bare_list",
                            key=wrap_key,
                            n_items=len(parsed),
                        )
                        return {wrap_key: parsed}

                if not isinstance(parsed, dict):
                    logger.warning("ollama.json_not_object", kind=type(parsed).__name__)
                    raise LLMJsonError(
                        f"expected JSON object, got {type(parsed).__name__}"
                    )
                return parsed

        # Unreachable — AsyncRetrying re-raises on exhaustion.
        raise LLMJsonError("Ollama chat_json exhausted retries")

    async def chat_text(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Run a chat completion and return the raw text content."""

        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=1, min=1, max=20),
            stop=stop_after_attempt(4),
            retry=retry_if_exception_type(_RETRY_EXC),
            reraise=True,
        ):
            with attempt:
                return await self._chat(
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=None,
                    extra=extra,
                )

        raise RuntimeError("Ollama chat_text exhausted retries")

    async def aclose(self) -> None:
        """Release underlying HTTP resources."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict[str, Any] | None,
        extra: dict[str, Any] | None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                temperature
                if temperature is not None
                else self._settings.llm_temperature
            ),
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if extra:
            kwargs.update(extra)

        logger.debug(
            "ollama.request",
            model=kwargs["model"],
            temperature=kwargs["temperature"],
            max_tokens=kwargs["max_tokens"],
            json_mode=response_format is not None,
        )

        resp = await self._client.chat.completions.create(**kwargs)
        if not resp.choices:
            raise LLMJsonError("Ollama returned no choices")

        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise LLMJsonError("Ollama returned empty content")
        return content
