"""Phone/null normalization — join-key parity with Campaign Intelligence src/normalize.py.

The call->lead join hinges on producing the IDENTICAL canonical mobile as the CI
lead master. Any divergence silently breaks the join, so this is a deliberate
verbatim port kept under the same test cases.
"""
from __future__ import annotations

import math
import re

_DIGITS = re.compile(r"\D")
_VALID_START = set("6789")
_NULL_TOKENS = {"", "NA", "N/A", "NULL", "NONE", "#N/A", "NAN", "-", "--"}


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


def clean_na(value: object) -> str | None:
    """Coerce 'NA'/blank/NaN to None; trim real strings."""
    if value is None or _is_nan(value):
        return None
    text = str(value).strip()
    if text.upper() in _NULL_TOKENS:
        return None
    return text


def normalize_mobile(value: object) -> str | None:
    """Return a canonical 10-digit Indian mobile string, or None if unreachable."""
    cleaned = clean_na(value)
    if cleaned is None:
        return None
    if cleaned.endswith(".0"):
        cleaned = cleaned[:-2]
    digits = _DIGITS.sub("", cleaned)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in _VALID_START:
        return None
    return digits


__all__ = ["normalize_mobile", "clean_na"]
