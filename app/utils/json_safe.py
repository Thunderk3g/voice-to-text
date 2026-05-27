"""
Robust JSON parsing for LLM outputs.

LLMs occasionally wrap JSON in ``` fences or add trailing prose. These helpers
recover well-formed JSON or raise a typed error the caller can handle.
"""

from __future__ import annotations

import json
import re
from typing import Any

import orjson


class LLMJsonError(ValueError):
    """Raised when an LLM response cannot be coerced into JSON."""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_llm_json(text: str) -> Any:
    """Parse a JSON object/array from an LLM response. Strips fences and prose."""
    if not text:
        raise LLMJsonError("empty LLM response")

    candidates: list[str] = []

    # 1. Fenced blocks
    candidates.extend(m.group(1).strip() for m in _FENCE_RE.finditer(text))

    # 2. Outer braces / brackets
    for open_c, close_c in [("{", "}"), ("[", "]")]:
        start = text.find(open_c)
        end = text.rfind(close_c)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    # 3. Raw text fallback
    candidates.append(text.strip())

    last_err: Exception | None = None
    for c in candidates:
        try:
            return orjson.loads(c)
        except (orjson.JSONDecodeError, ValueError) as e:
            last_err = e
            try:
                return json.loads(c)
            except (json.JSONDecodeError, ValueError) as e2:
                last_err = e2
                continue

    raise LLMJsonError(f"could not parse JSON from LLM output: {last_err}")
