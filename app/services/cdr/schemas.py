"""CDR loader schemas — config-driven column mapping + the per-call context.

These are *transport* models (Pydantic v2). The loader in ``loader.py`` builds
``CallContext`` instances; downstream enrichment/graph code consumes them by
field name. A site whose Crux export uses different headers only needs a custom
``CdrColumnMap`` — no code change (e.g. ``campaign='queue'``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CallDirection


class CdrColumnMap(BaseModel):
    """Logical-field -> CSV-header mapping for a Crux CDR export.

    Defaults match the canonical Crux header names. Override any field to adapt
    to a differently-named export without touching loader code.
    """

    model_config = ConfigDict(frozen=True)

    crux_call_id: str = "crux_call_id"
    caller_phone: str = "caller_number"
    agent_id: str = "agent_id"
    campaign: str = "campaign"
    started_at: str = "start_time"
    direction: str = "direction"


class CallContext(BaseModel):
    """Normalized per-call facts resolved from one CDR row (C6).

    ``crux_call_id`` is the required join anchor. ``caller_phone`` is already
    run through ``normalize_mobile`` (None when unreachable, e.g. a landline).
    """

    model_config = ConfigDict(frozen=True)

    crux_call_id: str
    caller_phone: str | None = None
    agent_id: str | None = None
    campaign: str | None = None
    started_at: datetime | None = None
    direction: CallDirection = Field(default=CallDirection.UNKNOWN)


DEFAULT_CDR_COLUMN_MAP = CdrColumnMap()


__all__ = ["CdrColumnMap", "CallContext", "DEFAULT_CDR_COLUMN_MAP"]
