"""Crux CDR loader — config-driven, pure parsing of call-detail records.

Public surface re-exported here so callers can ``from app.services.cdr import ...``.
"""

from __future__ import annotations

from app.services.cdr.loader import (
    CdrIndex,
    build_index,
    parse_cdr,
    parse_cdr_rows,
    row_to_context,
)
from app.services.cdr.schemas import (
    DEFAULT_CDR_COLUMN_MAP,
    CallContext,
    CdrColumnMap,
)

__all__ = [
    "CdrColumnMap",
    "CallContext",
    "CdrIndex",
    "parse_cdr",
    "parse_cdr_rows",
    "row_to_context",
    "build_index",
    "DEFAULT_CDR_COLUMN_MAP",
]
