# app/tests/unit/test_cdr_loader.py
"""Pure unit tests for the Crux CDR loader (T1 + T2).

No DB / network / pandas-IO-to-disk-required logic beyond tmp_path round-trips.
Phone normalization MUST stay byte-identical to app.utils.phone.normalize_mobile.
"""
from __future__ import annotations

import io
from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.enums import CallDirection
from app.services.cdr import (
    CallContext,
    CdrColumnMap,
    CdrIndex,
    DEFAULT_CDR_COLUMN_MAP,
    build_index,
    parse_cdr,
    parse_cdr_rows,
)
from app.services.cdr.loader import (
    _parse_direction,
    _parse_started_at,
    row_to_context,
)
from app.utils.phone import normalize_mobile


# ---------------------------------------------------------------------------
# T1 — CallDirection enum
# ---------------------------------------------------------------------------
def test_call_direction_members():
    assert {d.value for d in CallDirection} == {"inbound", "outbound", "unknown"}
    assert CallDirection.INBOUND.value == "inbound"
    assert CallDirection.OUTBOUND.value == "outbound"
    assert CallDirection.UNKNOWN.value == "unknown"


# ---------------------------------------------------------------------------
# T1 — CallContext / CdrColumnMap schemas
# ---------------------------------------------------------------------------
def test_call_context_defaults():
    ctx = CallContext(crux_call_id="25689211")
    assert ctx.crux_call_id == "25689211"
    assert ctx.caller_phone is None
    assert ctx.agent_id is None
    assert ctx.campaign is None
    assert ctx.started_at is None
    assert ctx.direction is CallDirection.UNKNOWN


def test_call_context_requires_crux_call_id():
    with pytest.raises(ValidationError):
        CallContext()  # type: ignore[call-arg]


def test_default_cdr_column_map_field_values():
    m = DEFAULT_CDR_COLUMN_MAP
    assert m == CdrColumnMap()
    assert m.crux_call_id == "crux_call_id"
    assert m.caller_phone == "caller_number"
    assert m.agent_id == "agent_id"
    assert m.campaign == "campaign"
    assert m.started_at == "start_time"
    assert m.direction == "direction"


# ---------------------------------------------------------------------------
# T2 — _parse_direction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MO", CallDirection.OUTBOUND),
        ("mo", CallDirection.OUTBOUND),
        ("out", CallDirection.OUTBOUND),
        ("OUTBOUND", CallDirection.OUTBOUND),
        ("MT", CallDirection.INBOUND),
        ("mt", CallDirection.INBOUND),
        ("in", CallDirection.INBOUND),
        ("INBOUND", CallDirection.INBOUND),
        ("", CallDirection.UNKNOWN),
        ("   ", CallDirection.UNKNOWN),
        (None, CallDirection.UNKNOWN),
        ("weird", CallDirection.UNKNOWN),
    ],
)
def test_parse_direction(raw, expected):
    assert _parse_direction(raw) is expected


# ---------------------------------------------------------------------------
# T2 — _parse_started_at
# ---------------------------------------------------------------------------
def test_parse_started_at_iso():
    assert _parse_started_at("2026-06-24T10:30:00") == datetime(2026, 6, 24, 10, 30, 0)


def test_parse_started_at_space_separated():
    assert _parse_started_at("2026-06-24 10:30:00") == datetime(2026, 6, 24, 10, 30, 0)


def test_parse_started_at_date_only():
    assert _parse_started_at("2026-06-24") == datetime(2026, 6, 24, 0, 0, 0)


def test_parse_started_at_none_and_blank():
    assert _parse_started_at(None) is None
    assert _parse_started_at("") is None
    assert _parse_started_at("NA") is None


def test_parse_started_at_garbage():
    assert _parse_started_at("not-a-date") is None
    assert _parse_started_at("24/13/2026") is None


# ---------------------------------------------------------------------------
# T2 — row_to_context: phone parity (must equal normalize_mobile verbatim)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+91 98765-43210",
        "+919876543210",
        "919876543210",
        "09876543210",
        "9876543210.0",
        "98765 43210",
        "0123456789",       # invalid start -> None
        "044-2345-6789",    # landline -> None
        "12345",            # too short -> None
        "NA",
        "",
    ],
)
def test_row_to_context_phone_parity(raw):
    row = {"crux_call_id": "C1", "caller_number": raw}
    ctx = row_to_context(row, DEFAULT_CDR_COLUMN_MAP)
    assert ctx.caller_phone == normalize_mobile(raw)


def test_row_to_context_landline_kept_with_none_phone():
    row = {"crux_call_id": "C9", "caller_number": "044-2345-6789"}
    ctx = row_to_context(row, DEFAULT_CDR_COLUMN_MAP)
    assert ctx.crux_call_id == "C9"          # record kept
    assert ctx.caller_phone is None          # but phone unusable


@pytest.mark.parametrize("token", ["NA", "#N/A", "N/A", "NULL", "", "-"])
def test_row_to_context_na_tokens_to_none(token):
    row = {
        "crux_call_id": "C2",
        "caller_number": "9876543210",
        "agent_id": token,
        "campaign": token,
    }
    ctx = row_to_context(row, DEFAULT_CDR_COLUMN_MAP)
    assert ctx.agent_id is None
    assert ctx.campaign is None


def test_row_to_context_absent_headers_tolerated():
    # Only crux_call_id present; every other lookup misses gracefully.
    ctx = row_to_context({"crux_call_id": "C3"}, DEFAULT_CDR_COLUMN_MAP)
    assert ctx.crux_call_id == "C3"
    assert ctx.caller_phone is None
    assert ctx.agent_id is None
    assert ctx.campaign is None
    assert ctx.started_at is None
    assert ctx.direction is CallDirection.UNKNOWN


def test_row_to_context_full_row():
    row = {
        "crux_call_id": "25689211",
        "caller_number": "+91 98765-43210",
        "agent_id": "AG-7",
        "campaign": "renewal-q2",
        "start_time": "2026-06-24 09:15:00",
        "direction": "MT",
    }
    ctx = row_to_context(row, DEFAULT_CDR_COLUMN_MAP)
    assert ctx.crux_call_id == "25689211"
    assert ctx.caller_phone == "9876543210"
    assert ctx.agent_id == "AG-7"
    assert ctx.campaign == "renewal-q2"
    assert ctx.started_at == datetime(2026, 6, 24, 9, 15, 0)
    assert ctx.direction is CallDirection.INBOUND


def test_row_to_context_custom_column_map():
    cmap = CdrColumnMap(crux_call_id="CallID", caller_phone="ANI", campaign="queue")
    row = {"CallID": "Z1", "ANI": "9988776655", "queue": "support"}
    ctx = row_to_context(row, cmap)
    assert ctx.crux_call_id == "Z1"
    assert ctx.caller_phone == "9988776655"
    assert ctx.campaign == "support"


# ---------------------------------------------------------------------------
# T2 — parse_cdr_rows: drops empty crux_call_id
# ---------------------------------------------------------------------------
def test_parse_cdr_rows_drops_empty_id():
    rows = [
        {"crux_call_id": "A", "caller_number": "9876543210"},
        {"crux_call_id": "", "caller_number": "9876543211"},
        {"crux_call_id": "NA", "caller_number": "9876543212"},
        {"crux_call_id": "B", "caller_number": "9876543213"},
    ]
    out = parse_cdr_rows(rows, DEFAULT_CDR_COLUMN_MAP)
    assert [c.crux_call_id for c in out] == ["A", "B"]


# ---------------------------------------------------------------------------
# T2 — parse_cdr + StringIO/tmp-file parity (leading-zero phone preserved)
# ---------------------------------------------------------------------------
_CSV = (
    "crux_call_id,caller_number,agent_id,campaign,start_time,direction\n"
    "25689211,09876543210,AG-1,renewal,2026-06-24 09:15:00,MT\n"
    "25689212,919812345678,NA,,2026-06-24,MO\n"
)


def test_parse_cdr_from_stringio_preserves_leading_zero_phone():
    contexts = parse_cdr(io.StringIO(_CSV), DEFAULT_CDR_COLUMN_MAP)
    assert len(contexts) == 2
    first = contexts[0]
    # 09876543210 -> strip leading 0 -> 9876543210 (would break if read as int)
    assert first.caller_phone == "9876543210"
    assert first.direction is CallDirection.INBOUND
    assert contexts[1].caller_phone == "9812345678"
    assert contexts[1].direction is CallDirection.OUTBOUND


def test_parse_cdr_stringio_vs_tmpfile_parity(tmp_path):
    from_stringio = parse_cdr(io.StringIO(_CSV), DEFAULT_CDR_COLUMN_MAP)
    p = tmp_path / "cdr.csv"
    p.write_text(_CSV, encoding="utf-8")
    from_file = parse_cdr(str(p), DEFAULT_CDR_COLUMN_MAP)
    assert len(from_stringio) == len(from_file)
    for a, b in zip(from_stringio, from_file):
        assert a == b
    # leading-zero phone survives the file path too
    assert from_file[0].caller_phone == "9876543210"


def test_parse_cdr_default_column_map_optional():
    # Column map argument is optional and defaults to DEFAULT_CDR_COLUMN_MAP.
    contexts = parse_cdr(io.StringIO(_CSV))
    assert [c.crux_call_id for c in contexts] == ["25689211", "25689212"]


# ---------------------------------------------------------------------------
# T2 — build_index / CdrIndex: last-wins dedupe, resolve hit/miss/None
# ---------------------------------------------------------------------------
def test_build_index_last_wins_dedupe():
    contexts = [
        CallContext(crux_call_id="DUP", caller_phone="9876543210"),
        CallContext(crux_call_id="OTHER", caller_phone="9811111111"),
        CallContext(crux_call_id="DUP", caller_phone="9822222222"),  # wins
    ]
    idx = build_index(contexts)
    assert isinstance(idx, CdrIndex)
    assert len(idx) == 2
    assert idx.resolve("DUP").caller_phone == "9822222222"


def test_cdr_index_resolve_hit_miss_none():
    idx = build_index([CallContext(crux_call_id="X1", caller_phone="9876543210")])
    assert idx.resolve("X1").crux_call_id == "X1"
    assert idx.resolve("nope") is None
    assert idx.resolve(None) is None


def test_cdr_index_contains_and_len():
    idx = build_index(
        [
            CallContext(crux_call_id="X1"),
            CallContext(crux_call_id="X2"),
        ]
    )
    assert "X1" in idx
    assert "X2" in idx
    assert "X3" not in idx
    assert len(idx) == 2


def test_build_index_from_parse_cdr():
    idx = build_index(parse_cdr(io.StringIO(_CSV)))
    assert len(idx) == 2
    assert "25689211" in idx
    assert idx.resolve("25689212").direction is CallDirection.OUTBOUND
