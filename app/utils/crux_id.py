"""Extract the Crux recording id (the numeric mp3 filename stem) so the call_id
survives ingest and a later CDR export can back-fill phone/agent/campaign."""
from __future__ import annotations

from pathlib import PurePath


def crux_call_id_from_name(name: str | None) -> str | None:
    if not name:
        return None
    stem = PurePath(name.replace("\\", "/")).stem.strip()
    return stem if stem.isdigit() else None


__all__ = ["crux_call_id_from_name"]
