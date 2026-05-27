"""
Load a pre-existing transcript JSON file into ``UtteranceSchema`` rows.

Expected shape — a JSON array of objects, each with at minimum::

    {
        "speaker": "AGENT" | "CUSTOMER" | "UNKNOWN" | "SPEAKER_00" | ...,
        "start_ts": 0.12,
        "end_ts": 4.56,
        "text": "Namaste, Bajaj Allianz se Rohit bol raha hoon",
        "language": "hi-en"   # optional — inferred via detect_language if missing
    }

The loader is intentionally tolerant: unknown speaker labels collapse to
``Speaker.UNKNOWN``, missing/blank language falls back to script detection,
and rows missing required fields are skipped with a structured warning.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from app.models.enums import Language, Speaker
from app.models.schemas import UtteranceSchema
from app.utils.lang import detect_language

log = structlog.get_logger(__name__)


def _coerce_speaker(raw: Any) -> Speaker:
    if raw is None:
        return Speaker.UNKNOWN
    s = str(raw).strip().upper()
    if s in {"AGENT", "A", "AGT"}:
        return Speaker.AGENT
    if s in {"CUSTOMER", "C", "CUST", "CLIENT"}:
        return Speaker.CUSTOMER
    return Speaker.UNKNOWN


def _coerce_language(raw: Any, text: str) -> Language:
    if raw is not None:
        candidate = str(raw).strip().lower()
        try:
            return Language(candidate)
        except ValueError:
            pass
    return detect_language(text)


def _coerce_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def load_transcript(call_id: UUID, json_path: str) -> list[UtteranceSchema]:
    """Read ``json_path`` and return ``UtteranceSchema`` rows for ``call_id``.

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` if
    the JSON is not a list. Individual malformed rows are skipped.
    """
    p = Path(json_path)
    if not p.exists():
        raise FileNotFoundError(f"transcript file not found: {json_path}")

    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, list):
        raise ValueError(
            f"transcript JSON must be a list of turns, got {type(data).__name__}"
        )

    out: list[UtteranceSchema] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            log.warning("transcript_row_skip", reason="not_dict", index=i)
            continue
        text = str(row.get("text", "") or "").strip()
        if not text:
            log.warning("transcript_row_skip", reason="empty_text", index=i)
            continue
        try:
            out.append(
                UtteranceSchema(
                    call_id=call_id,
                    speaker=_coerce_speaker(row.get("speaker")),
                    start_ts=_coerce_float(row.get("start_ts")),
                    end_ts=_coerce_float(row.get("end_ts")),
                    text=text,
                    language=_coerce_language(row.get("language"), text),
                    confidence=_coerce_float(row.get("confidence"), 1.0),
                    words=row.get("words") if isinstance(row.get("words"), list) else None,
                )
            )
        except Exception as exc:
            log.warning("transcript_row_skip", reason="schema_error", index=i, error=str(exc))
            continue

    log.info("transcript_loaded", call_id=str(call_id), path=str(p), n=len(out))
    return out


__all__ = ["load_transcript"]
