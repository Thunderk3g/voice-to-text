"""Unit tests for the pure helpers of the local pipeline runner (no STT/LLM/IO)."""
from __future__ import annotations

import pandas as pd

from app.scripts.run_pipeline_local import join_phone, lead_rows_for


class _Cdr:
    def __init__(self, caller_phone):
        self.caller_phone = caller_phone


def test_join_phone_cdr_primary():
    a = {"lead": {"phone": "9123456789", "grounded_fields": ["phone"]}}
    assert join_phone(a, _Cdr("+91 98765-43210")) == "9876543210"  # CDR wins, normalized


def test_join_phone_transcript_fallback_when_grounded():
    a = {"lead": {"phone": "09876543210", "grounded_fields": ["phone"]}}
    assert join_phone(a, None) == "9876543210"


def test_join_phone_none_when_ungrounded():
    a = {"lead": {"phone": "9876543210", "grounded_fields": []}}  # not grounded -> ignored
    assert join_phone(a, None) is None


def test_join_phone_none_when_cdr_phone_invalid_and_no_grounded():
    a = {"lead": {"phone": None, "grounded_fields": []}}
    assert join_phone(a, _Cdr("123")) is None


def test_lead_rows_for_matches_and_drops_helper_col():
    df = pd.DataFrame(
        {"MOBILE_NO": ["9876543210", "9876543210", "9999999999"],
         "LEAD_NO": ["L1", "L2", "L9"]}
    )
    df["_norm_mobile"] = df["MOBILE_NO"].map(lambda x: x)  # already normalized here
    rows = lead_rows_for("9876543210", df)
    assert {r["LEAD_NO"] for r in rows} == {"L1", "L2"}
    assert all("_norm_mobile" not in r for r in rows)  # helper col dropped


def test_lead_rows_for_empty_without_df_or_phone():
    assert lead_rows_for(None, None) == []
    assert lead_rows_for("9876543210", None) == []
