"""
Single LLM client for the entire backend.

Every LLM call -- text completion, structured JSON output, and audio
transcription -- must go through the `llm_service` singleton exported at the
bottom of this module. Do NOT instantiate provider SDKs (openai, groq,
google.generativeai, anthropic, etc.) elsewhere.

To swap providers (or models), edit the four `LLM_*` vars in the project root
`.env` and restart. The provider must expose an OpenAI-compatible endpoint
(Groq, Gemini, OpenAI, vLLM, Ollama, etc.) -- the SDK is the same.

Active provider config lives in `app.config.Settings` (`llm_*` fields).
"""

import json
import asyncio
import random
import time
from typing import Dict, Any, Optional, List, Type, TypeVar
from sqlalchemy.orm import Session
from ..models.tool_invocation import ToolInvocation
import logging
from pydantic import BaseModel, ValidationError
from ..config import settings
import httpx
from openai import AsyncOpenAI, AsyncAzureOpenAI, APIConnectionError, APIStatusError
try:
    from azure.cognitiveservices.speech import SpeechConfig, AudioConfig
    from azure.cognitiveservices.speech import SpeechRecognizer
    AZURE_SPEECH_AVAILABLE = True
except ImportError:
    AZURE_SPEECH_AVAILABLE = False
from app.core.tracing import get_azure_tracer
tracer = get_azure_tracer(__name__)

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


def strip_json_fence(text: str) -> str:
    """Remove ```json ... ``` markdown fences from LLM output, if present.

    Models often wrap JSON output in fenced code blocks even when asked
    not to. This helper centralises the unwrap so each caller doesn't
    re-implement the same string juggling (and so a fix to handle a new
    fence variant only needs to land in one place).
    """
    if text is None:
        return text
    s = text.strip()
    if s.startswith("```json"):
        s = s[len("```json"):].strip()
    elif s.startswith("```"):
        s = s[3:].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return s


def prepend_master_prompt(
    master_prompt: Optional[str],
    system_prompt: Optional[str]
) -> Optional[str]:
    """Prepend the workflow-version master_prompt to a system prompt.

    The agent runtime composes a per-workflow master prompt (organisation
    voice, regulatory disclaimers, etc.) onto each agent's own system
    prompt. Centralising the join character keeps the separator
    (`\\n\\n---\\n\\n`) consistent across agents -- voice, analytics,
    compliance, etc. -- so a downstream tweak only edits one function.
    """
    if not master_prompt:
        return system_prompt
    base = system_prompt or ""
    return f"{master_prompt}\n\n---\n\n{base}".rstrip()


class EmbeddingError(RuntimeError):
    """Raised by LLMService.embed_texts when embedding generation fails.

    Distinct from generate_embedding's no-raise contract: ingestion MUST fail
    loudly rather than persist NULL/empty vectors that silently break search.
    """


class LLMService:
    """Single entry point for all LLM and audio-transcription calls.

    Provider is configured via `LLM_*` env vars (see module docstring).
    All callers MUST use the `llm_service` singleton; never construct a
    provider client directly.
    """

    def __init__(self):
        self.reconfigure()

    def reconfigure(self) -> None:
        """(Re)build the LLM clients from the current ``settings`` values.

        Split out of ``__init__`` so the runtime Settings page can switch
        providers WITHOUT a restart: ``settings_service.apply_and_reconfigure``
        pushes new values onto the in-memory ``settings`` object and then calls
        this method on the process-wide ``llm_service`` singleton. It re-reads
        ``settings``, rebuilds the chat client, and resets the lazy embedding
        client so the next embed call rebuilds it. Supports Azure OpenAI and
        standard OpenAI. Idempotent — safe to call at startup and on every save.
        """
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model = settings.llm_model
        self.compliance_model = settings.llm_compliance_model
        self.transcription_model = settings.llm_transcription_model
        self.llm_provider = getattr(settings, 'llm_provider', 'openai').lower()
        self.stt_provider = getattr(settings, 'stt_provider', 'sarvam').lower()

        # Initialize LLM client (Azure or OpenAI)
        if not self.api_key:
             logger.warning("LLM_API_KEY is not set. LLM service will fail.")

        http_client = None
        if settings.llm_insecure_tls:
            logger.warning("WARNING: LLM_INSECURE_TLS=true -- TLS cert verification is DISABLED. Dev use only.")
            http_client = httpx.AsyncClient(verify=False, timeout=settings.llm_request_timeout_s)

        if self.llm_provider == 'azure':
            # Azure OpenAI client
            self.client = AsyncAzureOpenAI(
                api_key=self.api_key,
                api_version=getattr(settings, 'llm_azure_api_version', '2025-04-01-preview'),
                azure_endpoint=self.base_url,
                http_client=http_client,
                timeout=settings.llm_request_timeout_s,
            )
            logger.info(f"Initialized Azure OpenAI client (endpoint={self.base_url}, model={self.model})")
        else:
            # Standard OpenAI-compatible client
            client_kwargs = {
                "api_key": self.api_key,
                "base_url": self.base_url,
                "timeout": settings.llm_request_timeout_s,
                "http_client": http_client,
            }
            self.client = AsyncOpenAI(**client_kwargs)
            logger.info(f"Initialized OpenAI-compatible client (endpoint={self.base_url}, model={self.model})")

        # Embedding-specific creds + lazy client. Chat base_url (Groq/Gemini)
        # usually can't serve embedding models, so we keep a dedicated client.
        from app.config import settings as _settings
        self.embedding_api_key = _settings.effective_embedding_api_key
        self.embedding_base_url = _settings.effective_embedding_base_url
        self.embedding_model = _settings.embedding_model
        # Truncation target for generate_embedding (Gemini returns 3072; the
        # indexed Vector columns are 1536). MUST be set here — generate_embedding
        # reads self.embedding_dim, and a missing attr would be swallowed by its
        # no-raise contract, silently returning [] for every embedding.
        self.embedding_dim = _settings.embedding_dim
        # Reset the lazy embedding client so the next embed call rebuilds it
        # against the (possibly new) endpoint/key.
        self._embedding_client = None  # built on first use

    async def health_check(self, timeout_s: float = 5.0) -> bool:
        """Check if LLM service is available, bounded by `timeout_s`.

        `timeout_s` overrides the OpenAI client's per-request timeout for this
        single probe so callers (notably the FastAPI lifespan and the /health
        endpoint) don't block on a slow or unreachable LLM endpoint. Without
        this bound, an unreachable endpoint takes
        `llm_request_timeout_s * (retries+1)` seconds to give up -- typically
        ~3 minutes -- which used to hang the whole app at startup.
        """
        try:
            await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
                timeout=timeout_s,
            )
            logger.info(f"OK: LLM service available with model '{self.model}'")
            return True

        except asyncio.TimeoutError:
            logger.warning(f"LLM health check timed out after {timeout_s}s")
            return False
        except Exception as e:
            logger.warning(f"LLM health check failed: {str(e)}")
            return False

    @tracer.start_as_current_span("generate_response")
    async def generate_response(
        self,
        prompt: str,
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        raise_on_error: bool = False,
        **kwargs
    ) -> str:
        """Generate response from LLM.

        ``raise_on_error``: when True, an LLM failure re-raises instead of
        returning the compliance-shaped ``_get_fallback_response`` JSON.
        Callers whose output contract is NOT the compliance violations shape
        (e.g. the SEO grading agents, which JSON-schema-validate every reply)
        must set this — otherwise the fallback masquerades as model output,
        burns their repair retries, and surfaces as a misleading
        schema-validation error instead of "LLM unavailable".
        """
        messages = self._build_chat_messages(prompt, system_prompt, context)
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
                **{k: v for k, v in kwargs.items() if k != "temperature"}
            )
            response_text = response.choices[0].message.content.strip()

            # Log interaction
            logger.info(
                "llm.call",
                extra={
                    "prompt": prompt,
                    "response": response_text,
                    "system_prompt": system_prompt,
                    "context": context,
                    "model": self.model,
                },
            )

            return response_text

        except Exception as e:
            logger.error(f"LLM generation failed: {str(e)}")
            if raise_on_error:
                raise
            return self._get_fallback_response(prompt, context)

    @tracer.start_as_current_span("generate_with_tools")
    async def generate_with_tools(
        self,
        prompt: str,
        system_prompt: str = None,
        tools: List[Dict[str, Any]] = None,
        tool_executor=None,
        max_iterations: int = 5,
    ):
        """Standard OpenAI function-calling loop.

        ``tools`` are OpenAI function-calling specs
        (``{"type": "function", "function": {...}}``); ``tool_executor`` is
        an async ``(name: str, arguments: dict) -> str`` callable. The loop
        calls chat.completions with the tools, executes any returned
        tool_calls, appends the tool messages, and re-calls until the model
        answers in text (or ``max_iterations`` is exhausted, after which one
        final call is made WITHOUT tools to force a text answer).

        Returns ``(final_text, calls)`` where ``calls`` is a list of
        ``{tool_name, arguments, result, duration_ms}`` audit dicts.
        """
        messages = self._build_chat_messages(prompt, system_prompt)
        calls: List[Dict[str, Any]] = []

        for _ in range(max_iterations):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
            )
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                return (message.content or "").strip(), calls

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                start = time.perf_counter()
                try:
                    result = await tool_executor(tc.function.name, arguments)
                except Exception as exc:  # noqa: BLE001 — feed the error back to the model
                    logger.warning(
                        "Tool %r failed during generate_with_tools: %s",
                        tc.function.name,
                        exc,
                    )
                    result = f"Tool error: {exc}"
                duration_ms = int((time.perf_counter() - start) * 1000)
                if not isinstance(result, str):
                    result = json.dumps(result, default=str)
                calls.append(
                    {
                        "tool_name": tc.function.name,
                        "arguments": arguments,
                        "result": result,
                        "duration_ms": duration_ms,
                    }
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )

        # Iteration budget exhausted — force a final text answer.
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return (response.choices[0].message.content or "").strip(), calls

    @tracer.start_as_current_span("generate_structured_response")
    async def generate_structured_response(
        self,
        prompt: str,
        output_model: Type[T],
        system_prompt: str = None,
        context: Dict[str, Any] = None,
        execution_id: str = None,
        db: Session = None,
        tool_name: str = "llm_structured",
        is_compliance_check: bool = False
    ) -> T:
        """
        Generate a structured response validated against a Pydantic model.
        """
        # Append schema instructions to system prompt
        schema_instruction = (
            f"\nYou must output JSON that adheres to this schema:\n"
            f"{output_model.model_json_schema()}\n"
            f"Return ONLY the JSON object, no other text."
        )
        full_system_prompt = (system_prompt or "") + schema_instruction
        
        # We generally use response_format={"type": "json_object"} if supported,
        # but generic OpenAI compatible endpoints might variable support.
        # We will try robust prompting first (which we already added above).
        
        messages = self._build_chat_messages(prompt, full_system_prompt, context)
        start_time = time.time()
        
        # Simple retry logic (simplified from Ollama service)
        max_retries = 3
        current_messages = messages
        
        for attempt in range(max_retries):
            response_text = None
            try:
                # Route to fine-tuned model if this is a compliance rule check
                target_model = self.compliance_model if is_compliance_check else self.model

                # Use with_raw_response to access the full unmapped payload (crucial for Gemini's usageMetadata)
                response_wrapper = await self.client.chat.completions.with_raw_response.create(
                    model=target_model,
                    messages=current_messages,
                    temperature=0.2,  # Lower temperature for structured output
                    response_format={"type": "json_object"},
                )

                # Parse the standard ChatCompletion object
                response = response_wrapper.parse()

                # Guard against an empty/None payload BEFORE accessing
                # .choices[0].message.content.strip(): an empty choices list or
                # None content would raise an UNCAUGHT IndexError/AttributeError
                # that bypasses the retry/repair loop below. Raise JSONDecodeError
                # instead so the existing repair loop treats it as a retryable bad
                # response.
                if (
                    not response.choices
                    or response.choices[0].message.content is None
                    or response.choices[0].message.content.strip() == ""
                ):
                    raise json.JSONDecodeError(
                        "LLM returned empty choices or no message content", "", 0
                    )

                # Extract token usage
                token_usage = 0
                if hasattr(response, "usage") and response.usage:
                    token_usage = response.usage.total_tokens
                else:
                    # Fallback estimate
                    response_text_len = len(response.choices[0].message.content.strip())
                    token_usage = (len(prompt) + response_text_len) // 4

                response_text = response.choices[0].message.content.strip()

                # Log interaction
                logger.info(
                    "llm.call",
                    extra={
                        "prompt": prompt,
                        "response": response_text,
                        "system_prompt": system_prompt,
                        "context": context,
                        "model": target_model,
                    },
                )

                # Parse and Clean JSON (in case response format didn't work perfectly or extra text)
                response_text = strip_json_fence(response_text)

                # Validate (raises ValidationError or JSONDecodeError on failure)
                result = output_model.model_validate_json(response_text)

                end_time = time.time()

                # Record metrics if execution_id is provided
                if execution_id and db:
                    await self._record_tool_invocation(
                        db=db,
                        execution_id=execution_id,
                        tool_name=tool_name,
                        input_data={"prompt": prompt[:500], "system": system_prompt[:200] if system_prompt else None},
                        output_data=result.model_dump(mode="json"),
                        start_time=start_time,
                        end_time=end_time,
                        tokens=token_usage,
                    )

                return result

            except (APIConnectionError, APIStatusError) as transport_err:
                # Transport failure: do NOT mutate message history. Backoff + retry.
                logger.warning(
                    f"LLM transport error attempt {attempt + 1}/{max_retries}: {transport_err}"
                )
                if attempt == max_retries - 1:
                    raise
                delay = settings.llm_retry_base_delay_s * (2 ** attempt) + random.uniform(0, settings.llm_retry_base_delay_s)
                await asyncio.sleep(delay)

            except (ValidationError, json.JSONDecodeError) as validation_err:
                # The model returned bad JSON. Inject corrective feedback and retry.
                logger.warning(
                    f"LLM validation error attempt {attempt + 1}/{max_retries}: {validation_err}"
                )
                if attempt == max_retries - 1:
                    raise
                if response_text:
                    current_messages = current_messages + [
                        {"role": "assistant", "content": response_text},
                        {
                            "role": "user",
                            "content": (
                                "Previous response was invalid JSON or did not match the schema. "
                                f"Error: {validation_err}. Please CORRECT the JSON output."
                            ),
                        },
                    ]
                # else: model returned empty content -- no point quoting it back; just retry.
                delay = settings.llm_retry_base_delay_s * (2 ** attempt) + random.uniform(0, settings.llm_retry_base_delay_s)
                await asyncio.sleep(delay)

    def _build_chat_messages(
        self,
        user_prompt: str,
        system_prompt: str = None,
        context: Dict[str, Any] = None
    ) -> List[Dict[str, str]]:
        """Build messages array for Chat API."""
        messages = []

        # Add system message
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Add context as system message
        if context:
            context_str = json.dumps(context, indent=2)
            messages.append({
                "role": "system",
                "content": f"Context:\n{context_str}"
            })

        # Add user message
        messages.append({"role": "user", "content": user_prompt})

        return messages

    def _get_fallback_response(self, prompt: str, context: Dict[str, Any]) -> str:
        """Fallback response when LLM is unavailable."""
        return json.dumps({
            "violations": [],
            "overall_assessment": "AI analysis service temporarily unavailable. Please try again later.",
            "key_issues": ["AI service unavailable"]
        })
    
    async def generate_rules_from_context(
        self,
        search_results: List[Dict[str, Any]],
        industry: str,
        scope: str
    ) -> List[Dict[str, Any]]:
        """
        Generate structured compliance rules from search results.
        """
        # Map scope to category
        category_map = {
            "regulatory": "regulatory",
            "brand": "brand",
            "quality": "quality",
            "seo": "quality",
            "qualitative": "brand"
        }
        category = category_map.get(scope, "regulatory")
        
        # Build prompt for rule extraction
        prompt = self._build_rule_extraction_prompt(
            search_results=search_results,
            industry=industry,
            category=category
        )
        
        system_prompt = (
            "You are a compliance expert specializing in extracting structured rules "
            "from regulatory documents and best practices. Return ONLY valid JSON."
        )
        
        try:
            response = await self.generate_response(
                prompt=prompt,
                system_prompt=system_prompt,
                raise_on_error=True
            )
            
            # Parse JSON response
            rules = self._parse_rule_extraction_response(response, category)
            
            # Add source URLs
            for i, rule in enumerate(rules):
                if i < len(search_results):
                    rule["source_url"] = search_results[i].get("url", "")
            
            logger.info(f"Generated {len(rules)} rules for {category} from {len(search_results)} sources")
            
            return rules
            
        except Exception as e:
            # An LLM outage (raise_on_error=True above) must NOT masquerade as an
            # empty extraction. Surface the failure explicitly instead of returning
            # a falsely-clean []. A genuine 'no rules found' still returns [] on the
            # happy path because _parse_rule_extraction_response yields [] WITHOUT
            # raising after a successful generate_response.
            logger.error(f"Rule generation failed: {str(e)}")
            raise
    
    def _build_rule_extraction_prompt(
        self,
        search_results: List[Dict],
        industry: str,
        category: str
    ) -> str:
        """Build prompt for extracting rules from search results."""
        # Format search results
        sources = []
        for i, result in enumerate(search_results[:5], 1):  # Limit to top 5
            sources.append(
                f"Source {i}:\n"
                f"Title: {result.get('title', 'N/A')}\n"
                f"Content: {result.get('snippet', 'N/A')}\n"
            )
        
        sources_text = "\n\n".join(sources)
        
        return f"""Extract actionable compliance rules from the following sources for the {industry} industry.

{sources_text}

**Your task:**
Extract 3-5 specific, actionable compliance rules from these sources.

**Output Format (JSON only, no markdown):**
[
  {{
    "rule_text": "Clear, specific compliance requirement",
    "severity": "critical|high|medium|low",
    "keywords": ["keyword1", "keyword2"],
    "points_deduction": -20.0 (for critical) | -10.0 (high) | -5.0 (medium) | -2.0 (low),
    "confidence_score": 0.0-1.0
  }}
]

**Guidelines:**
- Focus on specific, testable requirements
- Extract exact language from sources where possible
- Assign severity based on regulatory importance
- Include relevant keywords for rule matching
- Set high confidence (0.8+) for explicit regulations
- Limit to 5 most important rules

Return ONLY the JSON array, no other text.
"""
    
    def _parse_rule_extraction_response(
        self,
        response: str,
        category: str
    ) -> List[Dict[str, Any]]:
        """Parse LLM response into structured rules."""
        try:
            # Clean response (remove markdown if present)
            response = strip_json_fence(response)

            # Simple fix for potential trailing commas or formatting issues could go here
            rules_data = json.loads(response)
            
            # Ensure it's a list
            if isinstance(rules_data, dict):
                rules_data = [rules_data]
            
            # Validate and add category
            validated_rules = []
            for rule in rules_data:
                if "rule_text" in rule:
                    validated_rules.append({
                        "category": category,
                        "rule_text": rule["rule_text"],
                        "severity": rule.get("severity", "medium"),
                        "keywords": rule.get("keywords", []),
                        "points_deduction": rule.get("points_deduction", -5.0),
                        "confidence_score": rule.get("confidence_score", 0.7)
                    })
            
            return validated_rules
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse rule extraction response: {str(e)}")
            logger.debug(f"Response was: {response[:500]}")
            return []

    async def analyze_line_for_violations(
        self,
        line_content: str,
        line_number: int,
        document_context: str,
        rules: List[Dict]
    ) -> Dict[str, Any]:
        """
        Analyze a single line for compliance violations.
        """
        from .prompts.deep_analysis_prompt import (
            build_deep_analysis_prompt,
            parse_line_analysis_response
        )
        
        # Build prompts
        prompts = build_deep_analysis_prompt(
            line_content=line_content,
            line_number=line_number,
            document_title=document_context,
            rules=rules
        )
        
        try:
            # Call LLM. raise_on_error=True so an LLM failure PROPAGATES instead
            # of returning the compliance-shaped fallback (violations: []), which
            # is indistinguishable from a genuinely clean line and would otherwise
            # silently score the line as fully compliant during deep analysis.
            response_text = await self.generate_response(
                prompt=prompts["user_prompt"],
                system_prompt=prompts["system_prompt"],
                raise_on_error=True,
            )

            # Parse response
            result = parse_line_analysis_response(response_text)

            logger.debug(f"Line {line_number} analysis: {len(result.get('violations', []))} violations found")

            return result

        except Exception as e:
            logger.error(f"Error analyzing line {line_number}: {str(e)}")
            # Mark the failure EXPLICITLY. Callers MUST treat a result carrying
            # "error" as "analysis did not run", never as "no violations found" —
            # otherwise an LLM outage masquerades as a perfectly compliant line.
            return {
                "relevance_context": "Analysis failed (LLM unavailable)",
                "violations": [],
                "error": "llm_failure",
            }

    @tracer.start_as_current_span("generate_embedding")
    async def generate_embedding(
        self,
        text: str,
        model: Optional[str] = None,
    ) -> List[float]:
        """Generate a single embedding vector for text.

        Used by KB-RAG retrieval (see app.services.agents.compliance.kb_retrieval)
        to embed the query before pgvector ANN search. The default model
        (``settings.embedding_model``) produces vectors whose dimensionality
        must match the kb_chunks.embedding column (see KbChunk docstring).

        Failure modes intentionally do NOT raise. Callers (retrieval) need an
        "empty embedding -> empty retrieval -> EMPTY_RETRIEVAL abstain" path
        to be cleanly distinguishable from a thrown exception that would
        500 the whole submission. We surface the same shape (an empty list)
        on both "no api key" and "provider blew up" so the caller can treat
        unconfigured the same as unavailable.
        """
        if not self.embedding_api_key:
            logger.warning("Embedding skipped: no embedding API key configured.")
            return []
        use_model = model or self.embedding_model
        try:
            resp = await self._get_embedding_client().embeddings.create(
                model=use_model, input=text
            )
            data = getattr(resp, "data", None) or []
            if not data:
                logger.warning("Embedding response had no data; returning empty vector.")
                return []
            vec = getattr(data[0], "embedding", None)
            if not vec:
                logger.warning("Embedding payload missing 'embedding' field; returning empty vector.")
                return []
            vec = list(vec)
            # Truncate to the configured dim. Gemini returns 3072; the indexed
            # Vector columns are 1536 (and pgvector ANN indexes cap at 2000).
            # Matryoshka/MRL embeddings keep meaning in their leading prefix, and
            # cosine search ignores magnitude, so a prefix slice is correct here.
            if self.embedding_dim and len(vec) > self.embedding_dim:
                vec = vec[: self.embedding_dim]
            return vec
        except Exception as exc:  # noqa: BLE001 -- see docstring on no-raise contract
            logger.warning("Embedding generation failed: %s", exc)
            return []

    def _get_embedding_client(self):
        """Build (once) an AsyncOpenAI client pointed at the embedding endpoint.

        Separate from the chat client because the chat base_url (Groq/Gemini)
        usually can't serve embedding models. Mirrors the chat client's TLS
        posture (settings.llm_insecure_tls).
        """
        if self._embedding_client is None:
            from openai import AsyncOpenAI
            import httpx
            from app.config import settings as _settings
            http_client = (
                httpx.AsyncClient(verify=False) if _settings.llm_insecure_tls else None
            )
            self._embedding_client = AsyncOpenAI(
                api_key=self.embedding_api_key,
                base_url=self.embedding_base_url,
                http_client=http_client,
            )
        return self._embedding_client

    @tracer.start_as_current_span("embed_texts")
    async def embed_texts(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """Batch-embed texts. Raises EmbeddingError on any failure or empty result.

        Returns [] for empty input. Used by KB ingestion, where a missing
        embedding must abort the ingest (NOT silently store NULL).
        """
        if not texts:
            return []
        if not self.embedding_api_key:
            raise EmbeddingError("Embedding skipped: no embedding API key configured.")
        use_model = model or self.embedding_model
        try:
            resp = await self._get_embedding_client().embeddings.create(
                model=use_model, input=texts
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Embedding request failed: {exc}") from exc
        data = getattr(resp, "data", None) or []
        if len(data) != len(texts):
            raise EmbeddingError(
                f"Embedding count mismatch: got {len(data)} for {len(texts)} inputs."
            )
        vectors: List[List[float]] = []
        for item in data:
            vec = getattr(item, "embedding", None)
            if not vec:
                raise EmbeddingError("Embedding payload missing 'embedding' field.")
            vec = list(vec)
            # Truncate to the configured dim, mirroring generate_embedding: the
            # model (e.g. gemini-embedding-001) returns 3072 dims but the indexed
            # Vector columns are embedding_dim (1536), and pgvector ANN indexes
            # cap at 2000. MRL/Matryoshka embeddings keep meaning in their leading
            # prefix and cosine ignores magnitude, so a prefix slice is correct.
            # Without this, inserts into call_questions.embedding / kb_chunks.embedding
            # fail with "expected 1536 dimensions, not 3072".
            if self.embedding_dim and len(vec) > self.embedding_dim:
                vec = vec[: self.embedding_dim]
            vectors.append(vec)
        return vectors

    @tracer.start_as_current_span("transcribe_audio")
    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str,
        filename: str = "audio",
    ) -> Dict[str, Any]:
        """Transcribe audio via the configured STT provider (Sarvam or Azure Speech).

        Returns a dict with at minimum:
            - "text": full transcript string
            - "segments": list of {start, end, text} chunks (may be empty if the
              provider does not return segments)
            - "language": detected ISO language code (may be None)

        Routes to Sarvam (Indian languages) or Azure Speech Services based on
        STT_PROVIDER environment variable. Raises on transport/auth failure;
        callers decide whether to fall back.
        """
        # Route to appropriate STT provider
        if self.stt_provider == 'azure':
            return await self._transcribe_audio_azure(audio_bytes, mime_type, filename)
        else:
            # Default to Sarvam (or Whisper via OpenAI-compatible endpoint)
            return await self._transcribe_audio_whisper(audio_bytes, mime_type, filename)

    async def _transcribe_audio_azure(
        self,
        audio_bytes: bytes,
        mime_type: str,
        filename: str = "audio",
    ) -> Dict[str, Any]:
        """Transcribe audio using Azure Speech Services."""
        if not AZURE_SPEECH_AVAILABLE:
            logger.error("Azure Speech SDK not available. Install: pip install azure-cognitiveservices-speech")
            raise RuntimeError("Azure Speech Services SDK not installed")

        try:
            import io
            # Azure Speech Services uses SpeechConfig + AudioConfig
            speech_key = getattr(settings, 'azure_speech_key')
            speech_region = getattr(settings, 'azure_speech_region', 'eastus')

            if not speech_key:
                raise ValueError("AZURE_SPEECH_KEY not configured")

            speech_config = SpeechConfig(subscription=speech_key, region=speech_region)
            speech_config.speech_recognition_language = getattr(settings, 'azure_speech_language', 'en-US')

            # Create audio config from bytes
            audio_stream = io.BytesIO(audio_bytes)
            audio_config = AudioConfig(use_default_microphone=False)
            audio_config.stream = audio_stream

            recognizer = SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

            # Perform recognition
            result = recognizer.recognize_once()

            if result.reason.name == 'RecognizedSpeech':
                return {
                    "text": result.text,
                    "segments": [],  # Azure doesn't return detailed segments
                    "language": speech_config.speech_recognition_language,
                }
            else:
                logger.error(f"Azure Speech recognition failed: {result.reason}")
                raise RuntimeError(f"Speech recognition failed: {result.reason}")

        except Exception as e:
            logger.error(f"Azure Speech transcription failed: {str(e)}")
            raise

    async def _transcribe_audio_whisper(
        self,
        audio_bytes: bytes,
        mime_type: str,
        filename: str = "audio",
    ) -> Dict[str, Any]:
        """Transcribe audio using Whisper-compatible endpoint (Sarvam or OpenAI).

        Note on language handling: Whisper auto-detect frequently labels Hindi
        customer-service calls as Urdu/Persian/Arabic because of shared
        phonology, which produces output in Arabic script. We detect that case
        and retry once with ``language="hi"`` so the transcript comes back in
        Devanagari. English audio is left alone -- it auto-detects cleanly.
        """
        ext_map = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/wave": "wav",
            "audio/ogg": "ogg",
            "audio/webm": "webm",
            "audio/mp4": "m4a",
            "audio/m4a": "m4a",
            "audio/flac": "flac",
        }
        ext = ext_map.get(mime_type.lower(), "mp3")

        # Disable the OpenAI SDK's default retry (2 attempts, ~0.4 s delay).
        # On 5xx the SDK retries almost immediately, which often clips Groq's
        # per-minute rate-limit window and turns a transient 500 into a 429.
        # We retry explicitly below with longer backoff and a fallback model.
        client = self.client.with_options(
            max_retries=0, timeout=settings.transcription_request_timeout_s
        )
        primary_model = self.transcription_model
        fallback_model = settings.llm_transcription_fallback_model

        response = None
        used_model = primary_model
        for attempt_idx, model in enumerate((primary_model, fallback_model)):
            try:
                response = await client.audio.transcriptions.create(
                    model=model,
                    file=(f"{filename}.{ext}", audio_bytes, mime_type),
                    response_format="verbose_json",
                )
                used_model = model
                break
            except APIStatusError as e:
                # 4xx (except 429) means the request itself is bad -- auth,
                # bad audio, model name. Retrying won't help.
                if e.status_code < 500 and e.status_code != 429:
                    raise
                if attempt_idx == 1 or fallback_model == primary_model:
                    raise
                logger.warning(
                    f"Transcription failed on model={model!r} with HTTP "
                    f"{e.status_code}; backing off 5s then retrying on "
                    f"fallback model={fallback_model!r}"
                )
                await asyncio.sleep(5.0)

        detected = (getattr(response, "language", "") or "").lower()
        # Hindi mis-detected as a related script-sharing language. Retry once,
        # pinning Hindi so we get Devanagari instead of Arabic script.
        if detected in {"ur", "urdu", "fa", "persian", "ar", "arabic"}:
            logger.info(
                f"Whisper detected language='{detected}'; retrying with "
                f"language='hi' to keep the transcript out of Urdu/Arabic script"
            )
            response = await client.audio.transcriptions.create(
                model=used_model,
                file=(f"{filename}.{ext}", audio_bytes, mime_type),
                response_format="verbose_json",
                language="hi",
            )

        text = getattr(response, "text", "") or ""
        raw_segments = getattr(response, "segments", None) or []
        segments = []
        for seg in raw_segments:
            segments.append({
                "start": getattr(seg, "start", 0.0) if not isinstance(seg, dict) else seg.get("start", 0.0),
                "end": getattr(seg, "end", 0.0) if not isinstance(seg, dict) else seg.get("end", 0.0),
                "text": (getattr(seg, "text", "") if not isinstance(seg, dict) else seg.get("text", "")) or "",
            })
        language = getattr(response, "language", None)
        return {"text": text, "segments": segments, "language": language}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
           await self.client.close()


    async def _record_tool_invocation(
        self,
        db: Session,
        execution_id: str,
        tool_name: str,
        input_data: Dict,
        output_data: Dict,
        start_time: float,
        end_time: float,
        tokens: int
    ):
        """Record tool invocation metrics to DB."""
        try:
            duration_ms = int((end_time - start_time) * 1000)
            cost = (tokens / 1000) * 0.0001 # Dummy cost model
            
            invocation = ToolInvocation(
                execution_id=execution_id,
                tool_name=tool_name,
                input_data=input_data,
                output_data=output_data,
                tokens_used=tokens,
                execution_time_ms=duration_ms,
                cost_usd=cost
            )
            db.add(invocation)
            db.commit()
            
        except Exception as e:
            logger.error(f"Failed to record tool invocation: {e}")

# Singleton instance
llm_service = LLMService()
