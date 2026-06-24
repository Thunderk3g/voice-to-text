# app/tests/unit/test_call_summary.py
import pandas as pd

from app.services.enrichment.call_summary import (
    CALL_SUMMARY_COLUMNS,
    DISPOSITION_SIGNIFICANCE,
    CallRecord,
    _collapse_disposition,
    _disposition_rank,
    _first_non_null,
    _record_phone,
    build_call_summary,
    write_call_summary,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _lead(**over):
    lead = {
        "full_name": None,
        "phone": None,
        "email": None,
        "age": None,
        "gender": None,
        "occupation": None,
        "education": None,
        "income_band": None,
        "pincode": None,
        "product_interest": None,
        "policy_no": None,
        "callback_time": None,
        "grounded_fields": [],
    }
    lead.update(over)
    return lead


def _analysis(**over):
    a = {
        "lead": _lead(),
        "disposition": "info_provided",
        "disposition_confidence": 0.5,
        "disposition_rationale": None,
        "sentiment": "neutral",
        "sentiment_confidence": 0.5,
        "escalation": False,
        "model": "test-model",
    }
    a.update(over)
    if "lead" in over:
        a["lead"] = over["lead"]
    return a


def _rec(call_id, call_date, phone=None, **analysis_over):
    return CallRecord(
        call_id=call_id,
        call_date=call_date,
        phone=phone,
        analysis=_analysis(**analysis_over),
    )


# ---------------------------------------------------------------------------
# C1 contract: CALL_SUMMARY_COLUMNS
# ---------------------------------------------------------------------------
def test_call_summary_columns_exact_names_and_order():
    assert CALL_SUMMARY_COLUMNS == [
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
    assert len(CALL_SUMMARY_COLUMNS) == 14


# ---------------------------------------------------------------------------
# T3: _record_phone grounding gate
# ---------------------------------------------------------------------------
def test_record_phone_ungrounded_lead_phone_is_ignored():
    # lead.phone present but NOT in grounded_fields -> not trusted, no CDR phone
    rec = _rec("c1", "2026-06-01", phone=None,
               lead=_lead(phone="9876543210", grounded_fields=[]))
    assert _record_phone(rec) is None


def test_record_phone_grounded_lead_phone_used():
    rec = _rec("c1", "2026-06-01", phone=None,
               lead=_lead(phone="9876543210", grounded_fields=["phone"]))
    assert _record_phone(rec) == "9876543210"


def test_record_phone_cdr_overrides_grounded_lead_phone():
    rec = _rec("c1", "2026-06-01", phone="+91 88888-88888",
               lead=_lead(phone="9876543210", grounded_fields=["phone"]))
    assert _record_phone(rec) == "8888888888"


def test_record_phone_cdr_junk_falls_back_to_grounded_lead():
    # CDR phone is unresolvable (landline-ish) -> fall back to grounded lead phone
    rec = _rec("c1", "2026-06-01", phone="0124-2233445",
               lead=_lead(phone="9876543210", grounded_fields=["phone"]))
    assert _record_phone(rec) == "9876543210"


def test_record_phone_cdr_junk_and_ungrounded_lead_is_none():
    rec = _rec("c1", "2026-06-01", phone="0124-2233445",
               lead=_lead(phone="9876543210", grounded_fields=[]))
    assert _record_phone(rec) is None


def test_record_phone_normalizes_cdr_value():
    rec = _rec("c1", "2026-06-01", phone="09876543210", lead=_lead())
    assert _record_phone(rec) == "9876543210"


# ---------------------------------------------------------------------------
# T3: _disposition_rank
# ---------------------------------------------------------------------------
def test_disposition_significance_order_escalation_most_significant():
    assert DISPOSITION_SIGNIFICANCE[0] == "escalation"
    assert DISPOSITION_SIGNIFICANCE[-1] == "other"


def test_disposition_rank_more_significant_has_lower_index():
    assert _disposition_rank("escalation") < _disposition_rank("complaint")
    assert _disposition_rank("complaint") < _disposition_rank("resolved")
    assert _disposition_rank("resolved") < _disposition_rank("other")


def test_disposition_rank_unknown_is_lowest():
    assert _disposition_rank("totally_unknown") == len(DISPOSITION_SIGNIFICANCE)
    assert _disposition_rank("totally_unknown") > _disposition_rank("other")


# ---------------------------------------------------------------------------
# T3: _collapse_disposition
# ---------------------------------------------------------------------------
def test_collapse_significance_beats_confidence():
    # complaint (more significant) with low conf beats info_provided with high conf
    recs = [
        _rec("c1", "2026-06-01", disposition="info_provided", disposition_confidence=0.99),
        _rec("c2", "2026-06-01", disposition="complaint", disposition_confidence=0.10),
    ]
    disp, conf = _collapse_disposition(recs)
    assert disp == "complaint"
    assert conf == 0.10


def test_collapse_newer_breaks_same_disposition_tie():
    # same disposition -> newer call date wins
    recs = [
        _rec("c1", "2026-06-01", disposition="complaint", disposition_confidence=0.40),
        _rec("c2", "2026-06-05", disposition="complaint", disposition_confidence=0.40),
    ]
    disp, conf = _collapse_disposition(recs)
    assert disp == "complaint"
    # winning record is the newer c2 (conf same here)
    assert conf == 0.40


def test_collapse_confidence_breaks_remaining_tie():
    # same disposition + same date -> higher confidence wins
    recs = [
        _rec("c1", "2026-06-01", disposition="complaint", disposition_confidence=0.30),
        _rec("c2", "2026-06-01", disposition="complaint", disposition_confidence=0.80),
    ]
    disp, conf = _collapse_disposition(recs)
    assert disp == "complaint"
    assert conf == 0.80


def test_collapse_returns_winning_confidence_not_mean():
    # winner .4 among [.4, .6] -> .4 (NOT mean 0.5)
    recs = [
        _rec("c1", "2026-06-05", disposition="complaint", disposition_confidence=0.40),
        _rec("c2", "2026-06-01", disposition="info_provided", disposition_confidence=0.60),
    ]
    disp, conf = _collapse_disposition(recs)
    assert disp == "complaint"
    assert conf == 0.40


# ---------------------------------------------------------------------------
# T3: _first_non_null
# ---------------------------------------------------------------------------
def test_first_non_null_scans_most_recent_first():
    recs = [
        _rec("c1", "2026-06-01", lead=_lead(occupation="old-job")),
        _rec("c2", "2026-06-05", lead=_lead(occupation="new-job")),
    ]
    assert _first_non_null(recs, "occupation") == "new-job"


def test_first_non_null_skips_empty_and_none():
    recs = [
        _rec("c1", "2026-06-05", lead=_lead(email=None)),
        _rec("c2", "2026-06-04", lead=_lead(email="")),
        _rec("c3", "2026-06-03", lead=_lead(email="found@example.com")),
    ]
    assert _first_non_null(recs, "email") == "found@example.com"


def test_first_non_null_tie_break_call_id_asc():
    # same date -> tie broken by call_id ascending
    recs = [
        _rec("c2", "2026-06-05", lead=_lead(full_name="Bravo")),
        _rec("c1", "2026-06-05", lead=_lead(full_name="Alpha")),
    ]
    assert _first_non_null(recs, "full_name") == "Alpha"


def test_first_non_null_returns_none_when_all_empty():
    recs = [
        _rec("c1", "2026-06-05", lead=_lead(pincode=None)),
        _rec("c2", "2026-06-04", lead=_lead(pincode="")),
    ]
    assert _first_non_null(recs, "pincode") is None


# ---------------------------------------------------------------------------
# T4: build_call_summary
# ---------------------------------------------------------------------------
def test_build_three_records_two_rows_sorted_by_phone():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210"),
        _rec("c2", "2026-06-02", phone="9876543210"),
        _rec("c3", "2026-06-03", phone="8123456789"),
    ]
    rows = build_call_summary(recs)
    assert len(rows) == 2
    assert [r["PHONE_NUMBER"] for r in rows] == ["8123456789", "9876543210"]
    # n_calls aggregated for the duplicated phone
    by_phone = {r["PHONE_NUMBER"]: r for r in rows}
    assert by_phone["9876543210"]["CALL_N_CALLS"] == "2"
    assert by_phone["8123456789"]["CALL_N_CALLS"] == "1"


def test_build_escalation_is_logical_or():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210", escalation=False),
        _rec("c2", "2026-06-02", phone="9876543210", escalation=True),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_ESCALATION"] == "true"


def test_build_no_escalation_is_false_literal():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210", escalation=False),
        _rec("c2", "2026-06-02", phone="9876543210", escalation=False),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_ESCALATION"] == "false"


def test_build_call_confidence_is_winning_confidence():
    # winner .4 among [.4, .6] -> '0.4'
    recs = [
        _rec("c1", "2026-06-05", phone="9876543210",
             disposition="complaint", disposition_confidence=0.40),
        _rec("c2", "2026-06-01", phone="9876543210",
             disposition="info_provided", disposition_confidence=0.60),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_DISPOSITION"] == "complaint"
    assert rows[0]["CALL_CONFIDENCE"] == "0.4"


def test_build_sentiment_from_winning_record():
    recs = [
        _rec("c1", "2026-06-05", phone="9876543210",
             disposition="complaint", disposition_confidence=0.40, sentiment="negative"),
        _rec("c2", "2026-06-01", phone="9876543210",
             disposition="info_provided", disposition_confidence=0.60, sentiment="positive"),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_SENTIMENT"] == "negative"


def test_build_confidence_rounded_three_dp():
    recs = [_rec("c1", "2026-06-01", phone="9876543210",
                 disposition_confidence=0.123456)]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_CONFIDENCE"] == "0.123"


def test_build_last_date_is_max():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210"),
        _rec("c2", "2026-06-09", phone="9876543210"),
        _rec("c3", "2026-06-05", phone="9876543210"),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_LAST_DATE"] == "2026-06-09"


def test_build_source_call_ids_date_desc():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210"),
        _rec("c2", "2026-06-09", phone="9876543210"),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_SOURCE_CALL_IDS"] == "c2,c1"


def test_build_row_keyset_matches_columns():
    recs = [_rec("c1", "2026-06-01", phone="9876543210")]
    rows = build_call_summary(recs)
    assert set(rows[0]) == set(CALL_SUMMARY_COLUMNS)


def test_build_excludes_unresolved_phone_record():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210"),
        # no CDR phone, lead phone ungrounded -> unresolved -> excluded
        _rec("c2", "2026-06-02", phone=None,
             lead=_lead(phone="8123456789", grounded_fields=[])),
    ]
    rows = build_call_summary(recs)
    assert len(rows) == 1
    assert rows[0]["PHONE_NUMBER"] == "9876543210"


def test_build_lead_fields_via_first_non_null():
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210",
             lead=_lead(full_name="Old Name", email=None)),
        _rec("c2", "2026-06-05", phone="9876543210",
             lead=_lead(full_name="New Name", email="new@example.com")),
    ]
    rows = build_call_summary(recs)
    assert rows[0]["CALL_LEAD_NAME"] == "New Name"
    assert rows[0]["CALL_LEAD_EMAIL"] == "new@example.com"


def test_build_empty_input_returns_empty_list():
    assert build_call_summary([]) == []


# ---------------------------------------------------------------------------
# T4: write_call_summary CSV round-trip
# ---------------------------------------------------------------------------
def test_write_round_trip_preserves_columns_and_values(tmp_path):
    recs = [
        _rec("c1", "2026-06-01", phone="9876543210",
             lead=_lead(full_name="Alice", pincode="0123456",
                        email="alice@example.com")),
        _rec("c2", "2026-06-02", phone="8123456789",
             lead=_lead(full_name="Bob")),
    ]
    rows = build_call_summary(recs)
    path = tmp_path / "call_summary.csv"
    write_call_summary(rows, str(path))

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # column order preserved
    assert list(df.columns) == CALL_SUMMARY_COLUMNS
    assert len(df) == 2

    by_phone = {r["PHONE_NUMBER"]: r for _, r in df.iterrows()}
    # leading digit/zeros preserved as string
    assert by_phone["9876543210"]["CALL_LEAD_PINCODE"] == "0123456"
    assert by_phone["9876543210"]["PHONE_NUMBER"] == "9876543210"
    # missing values -> '' not 'None'
    assert by_phone["8123456789"]["CALL_LEAD_EMAIL"] == ""
    assert "None" not in df.values.tolist().__str__()


def test_write_missing_values_are_empty_not_none(tmp_path):
    recs = [_rec("c1", "2026-06-01", phone="9876543210", lead=_lead())]
    rows = build_call_summary(recs)
    path = tmp_path / "out.csv"
    write_call_summary(rows, str(path))
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert df.iloc[0]["CALL_LEAD_NAME"] == ""
    assert df.iloc[0]["CALL_LEAD_EMAIL"] == ""


def test_write_empty_rows_writes_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    write_call_summary([], str(path))
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(df.columns) == CALL_SUMMARY_COLUMNS
    assert len(df) == 0
