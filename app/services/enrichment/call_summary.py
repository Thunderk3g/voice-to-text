"""Per-mobile call-summary builder (the `CALL_SUMMARY_COLUMNS` producer).

Pure functions over per-call ANALYSIS dicts (the exact
`app.workers.tasks._call_analysis_metadata` shape). Groups calls by a normalized
mobile join key, collapses each group to one row whose disposition is the
*most significant* call (ties broken by recency then confidence), and writes a
byte-stable CSV consumed verbatim by the Campaign Intelligence additive join.

No DB / Celery / network / LLM. All emitted values are STRINGS.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

from app.utils.phone import normalize_mobile

# C1 — exact names, exact order. CI redeclares this as CALL_COLUMNS.
CALL_SUMMARY_COLUMNS = [
    "PHONE_NUMBER",
    "CALL_DISPOSITION",
    "CALL_SENTIMENT",
    "CALL_ESCALATION",
    "CALL_N_CALLS",
    "CALL_LAST_DATE",
    "CALL_LEAD_NAME",
    "CALL_LEAD_EMAIL",
    "CALL_LEAD_OCCUPATION",
    "CALL_LEAD_INCOME_BAND",
    "CALL_LEAD_PRODUCT_INTEREST",
    "CALL_LEAD_PINCODE",
    "CALL_SOURCE_CALL_IDS",
    "CALL_CONFIDENCE",
]

# Disposition ranking, most significant -> least significant. Values are the
# CallDisposition enum `.value` strings carried by analysis['disposition'].
DISPOSITION_SIGNIFICANCE = [
    "escalation",
    "complaint",
    "follow_up_payment",
    "callback_requested",
    "service_request",
    "not_eligible",
    "not_interested",
    "dnd",
    "wrong_number",
    "resolved",
    "info_provided",
    "no_response",
    "other",
]

# Mapping from CALL_SUMMARY lead columns to Lead.model_dump() field names.
_LEAD_FIELD_COLUMNS = {
    "CALL_LEAD_NAME": "full_name",
    "CALL_LEAD_EMAIL": "email",
    "CALL_LEAD_OCCUPATION": "occupation",
    "CALL_LEAD_INCOME_BAND": "income_band",
    "CALL_LEAD_PRODUCT_INTEREST": "product_interest",
    "CALL_LEAD_PINCODE": "pincode",
}


@dataclass
class CallRecord:
    """One analyzed call awaiting summarization.

    `phone` is the CDR-side caller number (may be None); `analysis` is the C2
    ANALYSIS dict (`app.workers.tasks._call_analysis_metadata` output).
    """

    call_id: str
    call_date: str  # ISO YYYY-MM-DD (or '' when unknown)
    phone: str | None
    analysis: dict = field(default_factory=dict)


def _lead(rec: CallRecord) -> dict:
    lead = rec.analysis.get("lead") or {}
    return lead if isinstance(lead, dict) else {}


def _record_phone(rec: CallRecord) -> str | None:
    """Resolve the join mobile for one call.

    CDR phone wins when it normalizes; otherwise trust the extracted lead phone
    ONLY when 'phone' is in the lead's grounded_fields (C2 grounding gate).
    """
    lead = _lead(rec)
    cand = rec.phone if normalize_mobile(rec.phone) else None
    if cand is None:
        grounded = lead.get("grounded_fields") or []
        if "phone" in grounded:
            cand = lead.get("phone")
    return normalize_mobile(cand)


def _disposition_rank(disposition: str) -> int:
    """Index in DISPOSITION_SIGNIFICANCE (lower = more significant); unknown last."""
    try:
        return DISPOSITION_SIGNIFICANCE.index(disposition)
    except ValueError:
        return len(DISPOSITION_SIGNIFICANCE)


def _date_key(rec: CallRecord) -> str:
    """ISO date is lexicographically sortable; '' sorts before any real date."""
    return rec.call_date or ""


def _collapse_disposition(records: list[CallRecord]) -> tuple[str, float]:
    """Pick the winning call: most significant, then newest, then most confident.

    Returns ``(disposition, winning_confidence)`` — the WINNING call's
    disposition_confidence (NOT a mean), per C1.
    """

    def sort_key(rec: CallRecord):
        disp = rec.analysis.get("disposition", "")
        conf = rec.analysis.get("disposition_confidence", 0.0) or 0.0
        # min over (rank asc, date desc, conf desc) -> negate date/conf surrogates.
        return (_disposition_rank(disp), _neg_date(rec), -conf)

    winner = min(records, key=sort_key)
    disp = winner.analysis.get("disposition", "")
    conf = winner.analysis.get("disposition_confidence", 0.0) or 0.0
    return disp, conf


def _neg_date(rec: CallRecord):
    """A surrogate that sorts NEWER dates first under ascending `min`."""
    # Invert each char so that a later (lexicographically greater) date string
    # produces a smaller tuple. Pad-free: compare on the inverted code points,
    # with a tail marker so shorter (earlier-padded) strings order after.
    return tuple(-ord(c) for c in _date_key(rec))


def _first_non_null(records: list[CallRecord], field_name: str):
    """First non-empty lead value scanning most-recent-first (date desc, call_id asc)."""
    ordered = sorted(records, key=lambda r: (_neg_date(r), r.call_id))
    for rec in ordered:
        value = _lead(rec).get(field_name)
        if value is None:
            continue
        text = str(value).strip()
        if text == "":
            continue
        return value
    return None


def _winning_record(records: list[CallRecord]) -> CallRecord:
    def sort_key(rec: CallRecord):
        disp = rec.analysis.get("disposition", "")
        conf = rec.analysis.get("disposition_confidence", 0.0) or 0.0
        return (_disposition_rank(disp), _neg_date(rec), -conf)

    return min(records, key=sort_key)


def build_call_summary(records: list[CallRecord]) -> list[dict]:
    """Collapse per-call records into one CALL_SUMMARY row per resolved mobile."""
    groups: dict[str, list[CallRecord]] = {}
    for rec in records:
        phone = _record_phone(rec)
        if phone is None:
            continue
        groups.setdefault(phone, []).append(rec)

    rows: list[dict] = []
    for phone, group in groups.items():
        disposition, conf = _collapse_disposition(group)
        winner = _winning_record(group)

        escalation = any(
            bool(r.analysis.get("escalation")) for r in group
        )
        dates = [r.call_date for r in group if r.call_date]
        last_date = max(dates) if dates else ""

        # source call ids: most-recent-first (date desc, call_id asc)
        ordered = sorted(group, key=lambda r: (_neg_date(r), r.call_id))
        source_ids = ",".join(r.call_id for r in ordered)

        row = {
            "PHONE_NUMBER": phone,
            "CALL_DISPOSITION": disposition,
            "CALL_SENTIMENT": winner.analysis.get("sentiment", "") or "",
            "CALL_ESCALATION": "true" if escalation else "false",
            "CALL_N_CALLS": str(len(group)),
            "CALL_LAST_DATE": last_date,
            "CALL_SOURCE_CALL_IDS": source_ids,
            "CALL_CONFIDENCE": str(round(conf, 3)),
        }
        for col, lead_field in _LEAD_FIELD_COLUMNS.items():
            value = _first_non_null(group, lead_field)
            row[col] = "" if value is None else str(value)

        rows.append(row)

    rows.sort(key=lambda r: r["PHONE_NUMBER"])
    return rows


def write_call_summary(rows: list[dict], path: str) -> None:
    """Write rows as CSV with the canonical column order; None -> ''."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=CALL_SUMMARY_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in CALL_SUMMARY_COLUMNS})


__all__ = [
    "CALL_SUMMARY_COLUMNS",
    "DISPOSITION_SIGNIFICANCE",
    "CallRecord",
    "_record_phone",
    "_disposition_rank",
    "_collapse_disposition",
    "_first_non_null",
    "build_call_summary",
    "write_call_summary",
]
