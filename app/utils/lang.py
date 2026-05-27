"""
Language detection and normalization helpers.

We handle code-switching and Roman-script Hindi explicitly:
- script-based heuristic to spot Devanagari vs Latin vs Tamil vs Telugu
- langid + small custom rules to flag Hinglish / Roman-Hindi
"""

from __future__ import annotations

import re
import unicodedata

from app.models.enums import Language

# Unicode ranges we care about.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_TAMIL = re.compile(r"[஀-௿]")
_TELUGU = re.compile(r"[ఀ-౿]")
_LATIN = re.compile(r"[A-Za-z]")

# Common Romanized Hindi tokens — non-exhaustive but reliable signal.
_HINDI_ROMAN_TOKENS = {
    "hai", "haiye", "kya", "kyu", "kyun", "kaise", "kab", "kahaan", "kaha",
    "mera", "meri", "aap", "aapka", "aapki", "main", "mujhe", "humara", "hum",
    "nahi", "nahin", "haan", "ji", "matlab", "kitna", "kitni", "kaisa",
    "kaisi", "wala", "wali", "thik", "theek", "kuch", "chahiye", "milega",
    "milegi", "kar", "karna", "kara", "kari", "ho", "hua", "hui", "rha",
    "raha", "rahi", "policy", "premium", "claim", "nominee", "renewal",
}


def _has(rgx: re.Pattern[str], s: str) -> bool:
    return bool(rgx.search(s))


def detect_language(text: str) -> Language:
    """Heuristic, fast, dependency-light language detector.

    For full multilingual quality, callers may swap this for fastText.
    This default is deterministic and good enough to seed prompts.
    """
    if not text or not text.strip():
        return Language.OTHER

    s = unicodedata.normalize("NFC", text)

    if _has(_TAMIL, s):
        return Language.TAMIL
    if _has(_TELUGU, s):
        return Language.TELUGU

    has_dev = _has(_DEVANAGARI, s)
    has_lat = _has(_LATIN, s)

    if has_dev and has_lat:
        return Language.HINGLISH
    if has_dev:
        return Language.HINDI
    if has_lat:
        # Check for Romanized Hindi vs English
        tokens = re.findall(r"[A-Za-z]+", s.lower())
        if not tokens:
            return Language.OTHER
        hindi_hits = sum(1 for t in tokens if t in _HINDI_ROMAN_TOKENS)
        ratio = hindi_hits / max(len(tokens), 1)
        if ratio >= 0.20:
            return Language.HINGLISH if any(t.isascii() and len(t) > 2 for t in tokens) else Language.ROMAN_HINDI
        if ratio >= 0.10:
            return Language.HINGLISH
        return Language.ENGLISH

    return Language.OTHER


def add_e5_prefix(text: str, *, role: str = "passage") -> str:
    """multilingual-e5 requires 'query:' / 'passage:' prefixes."""
    role = role.lower()
    if role not in {"query", "passage"}:
        raise ValueError("role must be 'query' or 'passage'")
    return f"{role}: {text.strip()}"
