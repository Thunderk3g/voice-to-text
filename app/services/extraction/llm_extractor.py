"""
LLM-driven customer-question extractor.

Walks a call transcript (list of ``UtteranceSchema``), chunks it on turn
boundaries (~3500 tokens / ~12k chars), and runs each chunk through the
Groq JSON-mode chat completion using the prompts in
``app.prompts.extraction``.

Each returned question dict is validated against ``ExtractedQuestion``;
invalid items log a WARNING and are skipped (we never crash the pipeline
on one bad row). ``call_id`` is always stamped; ``utterance_id`` is
filled best-effort by locating a substring of ``raw_text`` inside an
utterance.

Prometheus counter ``extraction_processed`` is bumped per question with
``status`` in ``{"ok", "invalid", "skipped", "degraded", "ungrounded"}``;
``ok`` and ``degraded`` are mutually exclusive — a kept-but-degraded item
counts only as ``degraded``, and ``degraded`` is bumped only for items
that are actually kept (dropped items count as ``invalid``/``ungrounded``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.observability import extraction_processed, llm_calls
from app.models.enums import Language
from app.models.schemas import ExtractedQuestion, ExtractionResult, UtteranceSchema
from app.prompts import EXTRACTION_USER_TEMPLATE
from app.prompts.extraction import build_transcript_block
from app.prompts.extraction_lang import detect_dominant_language, system_prompt_for
from app.prompts.extraction_schema import EXTRACTION_RESPONSE_SCHEMA
from app.services.llm.groq_client import GroqClient
from app.utils.lang import detect_language

logger = structlog.get_logger(__name__)


# ~3500 tokens worth of English/Hindi text is roughly 12k chars. Chunking
# on turn boundaries keeps the speaker context intact.
_CHUNK_CHAR_BUDGET = 12_000

# Fields Gemma frequently omits; Pydantic then fills intent=other /
# confidence=0.0 / gloss=None silently. We keep such rows but flag them.
_DEFAULTED_FIELDS: tuple[str, ...] = ("intent", "confidence", "english_gloss")


class LLMExtractor:
    """Extract structured customer questions from a diarized call."""

    def __init__(self, client: GroqClient) -> None:
        self._client = client

    async def extract(
        self,
        call_id: UUID,
        utterances: list[UtteranceSchema],
    ) -> ExtractionResult:
        if not utterances:
            return ExtractionResult(
                call_id=call_id,
                questions=[],
                used_model=self._client.model,
                raw_response=None,
            )

        chunks = _chunk_utterances(utterances, _CHUNK_CHAR_BUDGET)

        # Detect the call's dominant language on the joined customer text
        # so we can pick a language-tuned system prompt for every chunk.
        customer_text = " ".join(
            u.text for u in utterances if u.speaker.value == "CUSTOMER"
        )
        lang_bucket = detect_dominant_language(customer_text or " ".join(u.text for u in utterances))
        system_prompt = system_prompt_for(lang_bucket)

        logger.info(
            "extractor.start",
            call_id=str(call_id),
            n_utterances=len(utterances),
            n_chunks=len(chunks),
            language_bucket=lang_bucket,
        )

        all_questions: list[ExtractedQuestion] = []
        raw_responses: list[str] = []

        for idx, chunk in enumerate(chunks):
            block = build_transcript_block(
                [
                    {
                        "speaker": u.speaker.value,
                        "text": u.text,
                        "start_ts": u.start_ts,
                    }
                    for u in chunk
                ]
            )
            user_prompt = EXTRACTION_USER_TEMPLATE.format(transcript=block)

            try:
                payload = await self._client.chat_json(
                    system=system_prompt,
                    user=user_prompt,
                    json_schema=EXTRACTION_RESPONSE_SCHEMA,
                    schema_name="extraction_result",
                )
                llm_calls.labels(purpose="extract", status="ok").inc()
            except Exception as exc:  # noqa: BLE001 — retries already exhausted
                logger.error(
                    "extractor.chunk_failed",
                    call_id=str(call_id),
                    chunk=idx,
                    error=str(exc),
                )
                extraction_processed.labels(status="error").inc()
                llm_calls.labels(purpose="extract", status="error").inc()
                continue

            raw_responses.append(_safe_repr(payload))
            questions = _coerce_questions(payload, call_id, chunk)
            all_questions.extend(questions)

        logger.info(
            "extractor.done",
            call_id=str(call_id),
            n_questions=len(all_questions),
        )

        return ExtractionResult(
            call_id=call_id,
            questions=all_questions,
            used_model=self._client.model,
            raw_response="\n---\n".join(raw_responses) if raw_responses else None,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _chunk_utterances(
    utterances: list[UtteranceSchema], budget_chars: int
) -> list[list[UtteranceSchema]]:
    """Pack utterances into chunks not exceeding ``budget_chars`` total.

    Chunks always break on turn boundaries — we never split an utterance.
    """
    chunks: list[list[UtteranceSchema]] = []
    current: list[UtteranceSchema] = []
    current_size = 0
    for u in utterances:
        size = len(u.text) + len(u.speaker.value) + 10
        if current and current_size + size > budget_chars:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(u)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def _coerce_questions(
    payload: dict[str, Any],
    call_id: UUID,
    chunk: list[UtteranceSchema],
) -> list[ExtractedQuestion]:
    """Validate the LLM payload and stamp ``call_id`` + best-effort utterance."""
    raw_list = payload.get("questions")
    if not isinstance(raw_list, list):
        logger.warning(
            "extractor.payload_missing_questions",
            call_id=str(call_id),
            keys=list(payload.keys()),
        )
        extraction_processed.labels(status="invalid").inc()
        return []

    out: list[ExtractedQuestion] = []
    now = datetime.now(timezone.utc)
    drop_ungrounded = get_settings().extraction_drop_ungrounded
    valid_languages = {m.value for m in Language}
    for item in raw_list:
        if not isinstance(item, dict):
            logger.warning(
                "extractor.question_not_object",
                call_id=str(call_id),
                kind=type(item).__name__,
            )
            extraction_processed.labels(status="invalid").inc()
            continue

        # Some models don't reliably honor the strict schema: they use alias
        # keys (query/question/...) and omit language.
        # Be liberal in what we accept so usable questions aren't dropped.
        if not item.get("raw_text"):
            for _alt in ("query", "question", "text", "utterance", "normalized_text"):
                if item.get(_alt):
                    item["raw_text"] = item[_alt]
                    break
        if not item.get("normalized_text") and item.get("raw_text"):
            item["normalized_text"] = item["raw_text"]
        if not item.get("language"):
            item["language"] = detect_language(
                item.get("normalized_text") or item.get("raw_text") or ""
            )
        elif item["language"] not in valid_languages:
            # Gemma sometimes emits codes outside the enum (e.g. "ur");
            # repair instead of letting validation drop a usable question.
            logger.warning(
                "extractor.language_coerced",
                call_id=str(call_id),
                original=item["language"],
            )
            item["language"] = detect_language(
                item.get("normalized_text") or item.get("raw_text") or ""
            )

        # Gemma frequently omits the classification fields entirely; keep the
        # row but make the degradation observable.
        missing_fields = [
            f for f in _DEFAULTED_FIELDS if item.get(f) in (None, "")
        ]
        if missing_fields:
            logger.warning(
                "extractor.fields_defaulted",
                call_id=str(call_id),
                missing=missing_fields,
                raw_text=(item.get("raw_text") or "")[:80],
            )

        # Stamp call_id + extraction time before validation so they're
        # always populated regardless of LLM output.
        item.setdefault("call_id", str(call_id))
        item["call_id"] = str(call_id)
        item.setdefault("extracted_at", now.isoformat())

        try:
            question = ExtractedQuestion.model_validate(item)
        except ValidationError as exc:
            logger.warning(
                "extractor.invalid_question",
                call_id=str(call_id),
                error=exc.errors(include_url=False)[:3],
                raw=item,
            )
            extraction_processed.labels(status="invalid").inc()
            continue

        # Best-effort utterance match by substring of raw_text.
        if question.utterance_id is None:
            question.utterance_id = _match_utterance_id(question.raw_text, chunk)

        # Hallucination guard: a real extraction's raw_text comes verbatim
        # from the call, so it must match some utterance. We can only judge
        # this when we have the chunk.
        if question.utterance_id is None and chunk:
            logger.warning(
                "extractor.ungrounded_question",
                call_id=str(call_id),
                raw_text=question.raw_text[:80],
                dropped=drop_ungrounded,
            )
            extraction_processed.labels(status="ungrounded").inc()
            if drop_ungrounded:
                continue

        if missing_fields:
            extraction_processed.labels(status="degraded").inc()
        out.append(question)
        if not missing_fields:
            extraction_processed.labels(status="ok").inc()

    return out


def _match_utterance_id(
    raw_text: str, chunk: list[UtteranceSchema]
) -> UUID | None:
    """Find the utterance whose text most likely sourced ``raw_text``."""
    if not raw_text:
        return None
    needle = raw_text.strip().lower()
    if not needle:
        return None

    # 1. Exact substring containment (cheap, common case).
    for u in chunk:
        if u.id is not None and needle in u.text.strip().lower():
            return u.id

    # 2. Reverse containment — utterance contained inside the normalized text.
    for u in chunk:
        if u.id is None:
            continue
        utext = u.text.strip().lower()
        if utext and utext in needle:
            return u.id

    return None


def _safe_repr(payload: dict[str, Any]) -> str:
    try:
        import orjson

        return orjson.dumps(payload).decode("utf-8")
    except Exception:  # noqa: BLE001
        return repr(payload)
