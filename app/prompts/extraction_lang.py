"""
Language-aware overlays for the master extraction system prompt.

We keep one canonical system prompt (``EXTRACTION_SYSTEM`` in
``app.prompts.extraction``) and prepend a short, language-specific HINT
section based on the detected dominant language of the call. This lifts
extraction quality on Hindi and Hinglish calls without rewriting the rules.

Detection lives in ``detect_dominant_language`` and is intentionally tiny —
a fast langid pass on the joined transcript. We bucket into four groups
(``hi``, ``en``, ``ur``, ``other``) because that's the resolution our prompt overlay
distinguishes; finer language IDs still flow through the model's own
``language`` output field.
"""

from __future__ import annotations

from typing import Literal

import structlog

from app.prompts.extraction import EXTRACTION_SYSTEM

logger = structlog.get_logger(__name__)


Bucket = Literal["hi", "en", "ur", "other"]


_OVERLAYS: dict[Bucket, str] = {
    "hi": (
        "LANGUAGE HINT: This call is primarily Hindi (Devanagari, Roman, or "
        "code-switched Hinglish). Customers often phrase questions indirectly "
        "(\"premium ka kya hua\", \"policy kab khatam hogi\"). Keep "
        "`normalized_text` in the same script the customer used. Use the "
        "`language` field to tag exact variant (hi / hi-roman / hi-en).\n\n"
    ),
    "en": (
        "LANGUAGE HINT: This call is primarily English. Customers may still "
        "drop occasional Hindi words for amounts or relationships (\"lakhs\", "
        "\"chacha\", \"beta\"). Tag those questions language=en unless the "
        "majority of the question is non-English.\n\n"
    ),
    "ur": (
        "LANGUAGE HINT: This call is primarily Urdu (Arabic script or Roman "
        "transliteration). Insurance terms appear as پالیسی (policy), "
        "پریمیم (premium), کلیم (claim). Customers phrase questions "
        "indirectly and politely. Keep `normalized_text` in the customer's "
        "script. `english_gloss` must ALWAYS be provided. Extract questions "
        "even when phrased as statements of confusion or requests.\n\n"
    ),
    "other": (
        "LANGUAGE HINT: Dominant language is non-Hindi non-English (Tamil, "
        "Telugu, etc). Preserve the customer's script in `normalized_text`. "
        "`english_gloss` must always be provided.\n\n"
    ),
}


def system_prompt_for(bucket: Bucket) -> str:
    """Return the master extraction system prompt with a language overlay prepended."""
    overlay = _OVERLAYS.get(bucket, "")
    return overlay + EXTRACTION_SYSTEM


# Small Hinglish marker set — common Latin-script Hindi function words. If a
# Latin-script transcript hits enough of these, treat it as a Hindi-bucket call
# even if langid returns "en". Keep it short and unambiguous (no "hi", "no",
# "to" etc which collide with English).
_HINGLISH_MARKERS = frozenset(
    {
        "kya", "hai", "hain", "ka", "ki", "ke", "ko", "mein", "mera", "meri",
        "mere", "aap", "aapka", "aapki", "hum", "humara", "hamari", "kab",
        "kaise", "kyun", "kyon", "nahi", "nahin", "haan", "ji", "kar",
        "karna", "karenge", "karna", "kiya", "kyu", "ho", "hoga", "hogi",
        "policy", "premium", "sir", "madam",  # only matter combined with markers
    }
)
# Subset whose presence does NOT count by itself — needs Hindi context.
_HINGLISH_AMBIG = frozenset({"policy", "premium", "sir", "madam"})


def detect_dominant_language(text: str) -> Bucket:
    """Classify the dominant language of a transcript into {hi, en, ur, other}.

    Order:
      1. Devanagari codepoint heuristic — wins immediately.
      1b. Arabic-script codepoint heuristic — wins immediately (→ ur).
      2. langid pass; "hi" → hi, "ur"/"ar"/"fa" → ur, "en" → en (subject to
         Hinglish override), everything else → other.
      3. Hinglish marker check — if langid said en but the text contains
         enough Latin-script Hindi function words, bump to "hi".
    """
    if not text or not text.strip():
        return "en"

    sample = text[:4000]

    # 1. Devanagari heuristic — a Hindi call in Devanagari has many ऀ-ॿ.
    devanagari = sum(1 for ch in sample if "ऀ" <= ch <= "ॿ")
    if devanagari >= max(20, len(sample) // 50):
        return "hi"

    # 1b. Arabic-script heuristic — Urdu calls transcribed in Arabic script.
    #     U+0600–U+06FF covers Arabic/Urdu letters incl. ٹ ڈ ے etc.
    arabic = sum(1 for ch in sample if "؀" <= ch <= "ۿ")
    if arabic >= max(20, len(sample) // 50):
        return "ur"

    # 2. langid
    langid_bucket: Bucket = "en"
    try:
        import langid  # type: ignore

        lang, _ = langid.classify(sample)
        if lang == "hi":
            return "hi"
        if lang in ("ur", "ar", "fa"):
            return "ur"
        langid_bucket = "en" if lang == "en" else "other"
    except Exception as exc:  # noqa: BLE001
        logger.debug("langid_failed", error=str(exc))

    # 3. Hinglish override
    tokens = [tok.lower().strip(".,?!\"'") for tok in sample.split()]
    strong_hits = sum(
        1 for t in tokens if t in _HINGLISH_MARKERS and t not in _HINGLISH_AMBIG
    )
    if strong_hits >= max(3, len(tokens) // 25):
        return "hi"

    return langid_bucket


__all__ = ["Bucket", "detect_dominant_language", "system_prompt_for"]
