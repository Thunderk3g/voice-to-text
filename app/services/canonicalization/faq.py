"""
FAQ canonicalization for a single semantic cluster.

The DB-side concerns (loading cluster metadata, fetching members, bumping
``version`` on an existing canonical FAQ) live in the persistence layer.
This module is *purely* responsible for picking the most-central examples
out of a cluster and asking the LLM to synthesize a canonical FAQ.

Workflow:
  1. Caller injects an async callable that returns the cluster's
     centroid + dominant metadata + a list of member examples
     (each with a normalized text + embedding vector).
  2. We sort by cosine distance to the centroid (ascending) — i.e. most
     central first — with the longest text as a tiebreaker for ties.
  3. We take the top 12, render them into the FAQ prompt, and validate
     the result against ``CanonicalFAQ``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable
from uuid import UUID, uuid4

import numpy as np
import structlog

from app.models.enums import Intent, Language
from app.models.schemas import CanonicalFAQ
from app.prompts import CANONICAL_FAQ_SYSTEM, CANONICAL_FAQ_USER_TEMPLATE
from app.services.llm.ollama_client import OllamaClient
from app.utils.vector import cosine_sim

logger = structlog.get_logger(__name__)


_MAX_EXAMPLES = 12


@dataclass(frozen=True)
class ClusterExample:
    """One member of a cluster used as an input example for canonicalization."""

    question_id: UUID
    text: str
    embedding: list[float]


@dataclass(frozen=True)
class ClusterContext:
    """Bundle returned by the DB-side fetcher describing the cluster."""

    cluster_id: UUID
    centroid: list[float]
    dominant_language: Language
    dominant_intents: list[Intent]
    total_members: int
    examples: list[ClusterExample]


# Async callable: cluster_id -> ClusterContext
GetClusterExamples = Callable[[UUID], Awaitable[ClusterContext]]


class FAQCanonicalizer:
    """LLM-driven canonical-FAQ generator for a single cluster."""

    def __init__(
        self,
        client: OllamaClient,
        get_cluster_examples_async: GetClusterExamples,
    ) -> None:
        self._client = client
        self._get_examples = get_cluster_examples_async

    async def canonicalize(self, cluster_id: UUID) -> CanonicalFAQ:
        ctx = await self._get_examples(cluster_id)
        chosen = _pick_central_examples(ctx, _MAX_EXAMPLES)

        if not chosen:
            raise ValueError(
                f"cluster {cluster_id} has no examples to canonicalize"
            )

        user_prompt = CANONICAL_FAQ_USER_TEMPLATE.format(
            language=ctx.dominant_language.value,
            intents=", ".join(i.value for i in ctx.dominant_intents) or "other",
            n=len(chosen),
            total=ctx.total_members,
            examples="\n".join(f"- {ex.text}" for ex in chosen),
        )

        payload = await self._client.chat_json(
            system=CANONICAL_FAQ_SYSTEM,
            user=user_prompt,
        )

        canonical_question = _str_or_none(payload.get("canonical_question")) or ""
        canonical_question_en = _str_or_none(payload.get("canonical_question_en"))
        suggested_answer = _str_or_none(payload.get("suggested_answer"))
        confidence = _clamp_unit(payload.get("confidence"))

        if not canonical_question:
            logger.warning(
                "canonicalizer.empty_canonical",
                cluster_id=str(cluster_id),
                payload_keys=list(payload.keys()),
            )
            # Fall back to the most-central example so we still produce a row.
            canonical_question = chosen[0].text

        now = datetime.now(timezone.utc)
        faq = CanonicalFAQ(
            id=uuid4(),
            cluster_id=cluster_id,
            canonical_question=canonical_question,
            canonical_question_en=canonical_question_en,
            suggested_answer=suggested_answer or None,
            language=ctx.dominant_language,
            confidence=confidence,
            version=1,  # DB layer increments on rewrites
            created_at=now,
            updated_at=now,
        )
        logger.info(
            "canonicalizer.done",
            cluster_id=str(cluster_id),
            confidence=confidence,
            language=ctx.dominant_language.value,
        )
        return faq


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _pick_central_examples(
    ctx: ClusterContext, k: int
) -> list[ClusterExample]:
    """Return the top-``k`` examples by cosine similarity to the centroid.

    Tiebreaker: longer text wins (more context for the LLM).
    """
    if not ctx.examples:
        return []
    centroid = np.asarray(ctx.centroid, dtype=np.float32)

    scored: list[tuple[float, int, int, ClusterExample]] = []
    for i, ex in enumerate(ctx.examples):
        try:
            sim = cosine_sim(np.asarray(ex.embedding, dtype=np.float32), centroid)
        except Exception:  # noqa: BLE001 — bad vector → push to the back
            sim = -1.0
        # Negative sim so we get descending order via stable sort; tiebreak by
        # negative text length so longer text wins among equal sims. The
        # enumerate index is a final, always-comparable tiebreaker so two fully
        # tied entries never fall through to comparing ClusterExample (frozen,
        # unorderable -> TypeError).
        scored.append((-sim, -len(ex.text), i, ex))

    scored.sort()
    return [tup[3] for tup in scored[:k]]


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clamp_unit(value: object) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f
