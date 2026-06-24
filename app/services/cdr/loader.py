"""Pure Crux CDR parsing — rows -> ``CallContext`` -> ``CdrIndex``.

No DB / network / Celery / LLM. CSV reads use ``dtype=str`` +
``keep_default_na=False`` so blank cells stay ``''`` and phone numbers keep
their leading digits (e.g. ``09876543210`` is not coerced to an int).

All phone/null handling is delegated to ``app.utils.phone`` — this module adds
NO phone logic of its own (join-key parity with Campaign Intelligence).
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

import pandas as pd

from app.models.enums import CallDirection
from app.services.cdr.schemas import (
    DEFAULT_CDR_COLUMN_MAP,
    CallContext,
    CdrColumnMap,
)
from app.utils.phone import clean_na, normalize_mobile

# ``datetime.fromisoformat`` handles ISO 8601 (incl. the space-separated and
# date-only forms on 3.11+). These fallbacks cover a couple of common Crux
# layouts that fromisoformat would still reject.
_DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d/%m/%Y",
)

_INBOUND_TOKENS = {"in", "inbound", "mt"}
_OUTBOUND_TOKENS = {"out", "outbound", "mo"}


def _parse_started_at(raw: object) -> datetime | None:
    """Best-effort parse of a CDR timestamp cell -> ``datetime`` or ``None``."""
    text = clean_na(raw)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_direction(raw: object) -> CallDirection:
    """Map a CDR direction token (MT/MO/in/out/...) to ``CallDirection``."""
    text = clean_na(raw)
    if text is None:
        return CallDirection.UNKNOWN
    token = text.lower()
    if token in _INBOUND_TOKENS:
        return CallDirection.INBOUND
    if token in _OUTBOUND_TOKENS:
        return CallDirection.OUTBOUND
    return CallDirection.UNKNOWN


def row_to_context(
    row: Mapping[str, object],
    column_map: CdrColumnMap = DEFAULT_CDR_COLUMN_MAP,
) -> CallContext:
    """Build a ``CallContext`` from one CDR row using ``column_map``.

    Uses ``row.get(...)`` throughout so absent headers degrade gracefully to
    ``None`` rather than raising.
    """
    return CallContext(
        crux_call_id=clean_na(row.get(column_map.crux_call_id)) or "",
        caller_phone=normalize_mobile(row.get(column_map.caller_phone)),
        agent_id=clean_na(row.get(column_map.agent_id)),
        campaign=clean_na(row.get(column_map.campaign)),
        started_at=_parse_started_at(row.get(column_map.started_at)),
        direction=_parse_direction(row.get(column_map.direction)),
    )


def parse_cdr_rows(
    rows: Iterable[Mapping[str, object]],
    column_map: CdrColumnMap = DEFAULT_CDR_COLUMN_MAP,
) -> list[CallContext]:
    """Convert raw row mappings to contexts, dropping rows with no call id."""
    contexts: list[CallContext] = []
    for row in rows:
        ctx = row_to_context(row, column_map)
        if not ctx.crux_call_id:
            continue
        contexts.append(ctx)
    return contexts


def parse_cdr(
    source: object,
    column_map: CdrColumnMap = DEFAULT_CDR_COLUMN_MAP,
) -> list[CallContext]:
    """Read a Crux CDR (path or file-like) into ``CallContext`` objects.

    ``dtype=str`` + ``keep_default_na=False`` keep cells verbatim so blanks stay
    ``''`` and phones keep leading zeros.
    """
    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    records = frame.to_dict("records")
    return parse_cdr_rows(records, column_map)


class CdrIndex:
    """Last-wins lookup of ``crux_call_id`` -> ``CallContext``."""

    def __init__(self, by_id: dict[str, CallContext]) -> None:
        self._by_id = by_id

    def resolve(self, crux_call_id: object) -> CallContext | None:
        """Return the context for ``crux_call_id``, or ``None`` if absent."""
        if crux_call_id is None:
            return None
        return self._by_id.get(str(crux_call_id))

    def __contains__(self, crux_call_id: object) -> bool:
        return crux_call_id is not None and str(crux_call_id) in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)


def build_index(contexts: Iterable[CallContext]) -> CdrIndex:
    """Index contexts by ``crux_call_id`` (later rows overwrite earlier ones)."""
    by_id: dict[str, CallContext] = {}
    for ctx in contexts:
        by_id[ctx.crux_call_id] = ctx
    return CdrIndex(by_id)


__all__ = [
    "row_to_context",
    "parse_cdr_rows",
    "parse_cdr",
    "build_index",
    "CdrIndex",
]
