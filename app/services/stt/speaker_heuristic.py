"""
Speaker assignment heuristic — runs after Sarvam transcription.

Sarvam's sync ``/speech-to-text`` does not return speaker labels, so we
need to recover AGENT vs CUSTOMER ourselves. We do NOT bring back
pyannote; the v2t scope is to keep STT external. Instead, we score each
chunk against three signals and tag the speaker with the better-scoring
label.

Signals
-------
1. **Greeting tokens in the first turn.** Agents in BFSI scripts open with
   one of ``namaste``, ``good morning``, ``hello``, ``thank you for
   calling``, ``Bajaj Allianz`` (or any brand name passed in). The first
   chunk that hits any of these is locked to AGENT; the inverse label is
   bound to the next chunk and we propagate from there.

2. **Average words per turn.** Agents read scripts; customers are terser
   and ask more questions. After the greeting lock, we re-score chunks
   whose neighbour assignment is ambiguous using a higher-word-count →
   AGENT rule.

3. **Interrogative density.** Customer turns are interrogative-heavy
   (``kya``, ``kyun``, ``kaise``, ``kab``, ``how``, ``when``, ``?``).

The result is a *good-enough* baseline. When Sarvam's Batch API
(diarization included) gets wired in, this module can be skipped via the
``v2t.skip_speaker_heuristic`` flag — for now it's always on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.models.enums import Speaker
from app.models.schemas import UtteranceSchema


# Brand placeholders — augment via `extra_brand_tokens` arg if needed.
_DEFAULT_GREETING_TOKENS = (
    "namaste",
    "namaskar",
    "good morning",
    "good afternoon",
    "good evening",
    "hello",
    "hi",
    "thank you for calling",
    "bajaj allianz",
    "bajaj",
    "hdfc life",
    "icici prudential",
    "lic",
    "sbi life",
    "max life",
    "tata aia",
    "kotak life",
    "aviva",
    "policy bazaar",
)

_INTERROGATIVE_TOKENS = (
    "kya", "kyu", "kyun", "kyon", "kaise", "kaisa", "kaisi",
    "kab", "kahan", "kahaan", "kahaa", "kitna", "kitni",
    "how", "what", "why", "when", "where", "which",
    "?",
    # Tamil / Telugu interrogatives (common ones)
    "enna", "eppadi", "eppo", "yenna", "evaru", "ela", "endhuku",
)

_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def _lower(s: str) -> str:
    return (s or "").lower()


def _has_any(text: str, tokens: Iterable[str]) -> bool:
    t = _lower(text)
    return any(tok in t for tok in tokens)


def _count_any(text: str, tokens: Iterable[str]) -> int:
    t = _lower(text)
    return sum(t.count(tok) for tok in tokens)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


@dataclass(frozen=True)
class _Scores:
    greeting: bool
    word_count: int
    interrog: int


def _score(u: UtteranceSchema, brand_tokens: tuple[str, ...]) -> _Scores:
    return _Scores(
        greeting=_has_any(u.text, brand_tokens + _DEFAULT_GREETING_TOKENS),
        word_count=_word_count(u.text),
        interrog=_count_any(u.text, _INTERROGATIVE_TOKENS),
    )


def assign_speakers(
    utterances: list[UtteranceSchema],
    *,
    extra_brand_tokens: tuple[str, ...] = (),
) -> list[UtteranceSchema]:
    """Return a new list of utterances with speakers assigned.

    Does not mutate the input.
    """
    if not utterances:
        return []

    scores = [_score(u, extra_brand_tokens) for u in utterances]

    # 1) Find the FIRST chunk that contains a greeting token. That speaker is AGENT.
    first_greeting_idx: int | None = next(
        (i for i, s in enumerate(scores) if s.greeting),
        None,
    )

    # 2) Initialise label sequence. If the greeting is in chunk 0, AGENT starts
    # the call; otherwise pick the speaker who talks more (the script reader).
    n = len(utterances)
    labels: list[Speaker] = [Speaker.UNKNOWN] * n

    if first_greeting_idx is not None:
        labels[first_greeting_idx] = Speaker.AGENT
    else:
        # Fallback: chunk with the highest word count is AGENT.
        wc_idx = max(range(n), key=lambda i: scores[i].word_count)
        labels[wc_idx] = Speaker.AGENT

    # 3) Propagate by alternation, then adjust each chunk by its own signals.
    for i in range(n):
        if labels[i] != Speaker.UNKNOWN:
            continue
        # Default: alternate from the nearest already-labelled neighbour.
        neighbour = _nearest_labelled(labels, i)
        if neighbour is not None:
            dist = abs(neighbour - i)
            labels[i] = labels[neighbour] if dist % 2 == 0 else _flip(labels[neighbour])
        else:
            labels[i] = Speaker.CUSTOMER

    # 4) Per-chunk correction: interrogative-heavy short chunks lean CUSTOMER;
    # very long chunks lean AGENT (scripts). We only flip when the alternation
    # default and the per-chunk signal disagree strongly.
    for i, s in enumerate(scores):
        long_chunk = s.word_count >= 25
        questiony = s.interrog >= 2 and s.word_count <= 30
        if long_chunk and labels[i] == Speaker.CUSTOMER:
            labels[i] = Speaker.AGENT
        elif questiony and labels[i] == Speaker.AGENT:
            labels[i] = Speaker.CUSTOMER

    return [
        u.model_copy(update={"speaker": labels[i]}) for i, u in enumerate(utterances)
    ]


def map_speaker_roles(
    utterances: list[UtteranceSchema],
    *,
    extra_brand_tokens: tuple[str, ...] = (),
) -> list[UtteranceSchema]:
    """Label diarized utterances: decide which ``speaker_id`` is the AGENT.

    Unlike :func:`assign_speakers` (which has to *segment* speakers too),
    diarization already grouped the turns — we only score each speaker_id as
    a whole: greeting tokens weigh heaviest (agents open with the script),
    then total words spoken (agents talk more), minus interrogative density
    (customers ask the questions). Utterances without a ``speaker_id`` fall
    back to the full heuristic.
    """
    if not utterances:
        return []
    if not any(u.speaker_id for u in utterances):
        return assign_speakers(utterances, extra_brand_tokens=extra_brand_tokens)

    ids = sorted({u.speaker_id for u in utterances if u.speaker_id})
    if len(ids) == 1:
        # Single detected speaker — everything is the customer's side
        # is as wrong as everything-agent; AGENT matches one-sided
        # outbound scripts more often.
        only = ids[0]
        return [
            u.model_copy(update={"speaker": Speaker.AGENT if u.speaker_id == only else Speaker.UNKNOWN})
            for u in utterances
        ]

    brand = extra_brand_tokens + _DEFAULT_GREETING_TOKENS
    first_sid = min(utterances, key=lambda u: u.start_ts).speaker_id
    scores: dict[str, float] = {}
    for sid in ids:
        turns = [u for u in utterances if u.speaker_id == sid]
        greeting_hits = sum(1 for u in turns if _has_any(u.text, brand))
        words = sum(_word_count(u.text) for u in turns)
        interrog = sum(_count_any(u.text, _INTERROGATIVE_TOKENS) for u in turns)
        # First speaker of the call gets a nudge — agents answer/open the line.
        opens_call = 1.0 if sid == first_sid else 0.0
        scores[sid] = greeting_hits * 100.0 + words * 0.1 - interrog * 2.0 + opens_call * 10.0

    agent_id = max(ids, key=lambda sid: scores[sid])

    def _role(u: UtteranceSchema) -> Speaker:
        if not u.speaker_id:
            return Speaker.UNKNOWN
        return Speaker.AGENT if u.speaker_id == agent_id else Speaker.CUSTOMER

    return [u.model_copy(update={"speaker": _role(u)}) for u in utterances]


def _nearest_labelled(labels: list[Speaker], i: int) -> int | None:
    n = len(labels)
    for d in range(1, n):
        for j in (i - d, i + d):
            if 0 <= j < n and labels[j] != Speaker.UNKNOWN:
                return j
    return None


def _flip(s: Speaker) -> Speaker:
    if s == Speaker.AGENT:
        return Speaker.CUSTOMER
    if s == Speaker.CUSTOMER:
        return Speaker.AGENT
    return Speaker.UNKNOWN


__all__ = ["assign_speakers", "map_speaker_roles"]
