# Call Intelligence P0–P2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-call analyzer that extracts `{lead, disposition, sentiment}` in one Groq pass over the whole transcript, retain the Crux call id at ingest, and stand up the join-key + crosswalk + slice-ingestion scaffolding — proving the loop on a slice before corpus-scale spend.

**Architecture:** Extend the existing Celery pipeline. Clone the `LLMExtractor` scaffolding into a `CallAnalyzer` that runs one whole-transcript pass (both speakers) and returns a single object. Persist to `calls.metadata` JSONB first (no migration). Reuse `GroqClient.chat_json` strict-JSON mode, the language overlays, the grounding guard, and the `_stage`/`set_call_status`/`_next`/`run_async` task pattern.

**Tech Stack:** Python 3.11, FastAPI, Celery, SQLAlchemy 2.0 async + JSONB, Pydantic v2, Groq `openai/gpt-oss-120b` (OpenAI-compatible), pytest + respx.

Spec: `docs/superpowers/specs/2026-06-24-call-intelligence-enrichment-graph-design.md`.

## Global Constraints

- Python ≥ 3.11. Run every test with `./.venv/Scripts/python -m pytest <path> -q` (system Python is 3.10 — do NOT use it).
- Single source of truth for enums is `app/models/enums.py` (`StrEnum`). Do NOT redefine enums elsewhere.
- The call→lead join key is a normalized 10-digit Indian mobile. `app/utils/phone.normalize_mobile` MUST be byte-for-byte behavior-identical to Campaign Intelligence `src/normalize.py` (port its test cases verbatim).
- Call-derived data is ADDITIVE. Never overwrite the LMS `DISPOSITION`; new outputs live under `calls.metadata` / new columns.
- LLM calls go through `app.services.llm.groq_client.GroqClient.chat_json(system, user, *, json_schema=..., schema_name=...)`. Mock the HTTP layer with `respx` against `${LLM_BASE_URL}/chat/completions` (see `app/tests/unit/test_extractor.py`).
- TDD per step: write failing test → run it & confirm failure → minimal implementation → run & confirm pass → commit.
- Commit message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `app/utils/phone.py` | `normalize_mobile`, `clean_na` — join-key parity with CI | create |
| `app/utils/crux_id.py` | `crux_call_id_from_name` — pull 8-digit Crux id from a filename | create |
| `app/api/routes/ingest.py` | stamp `metadata.extra.crux_call_id` on upload | modify |
| `app/models/enums.py` | `CallDisposition`, `SentimentLabel`, 2 new `CallStatus` | modify |
| `app/models/schemas.py` | `Lead`, `CallAnalysis`, `CallAnalysisResult` | modify |
| `app/prompts/call_analysis.py` | system prompt + user template + transcript block | create |
| `app/prompts/call_analysis_schema.py` | `CALL_ANALYSIS_SCHEMA` (strict JSON) | create |
| `app/services/extraction/call_analysis.py` | `CallAnalyzer` — one-pass analyzer | create |
| `app/data/disposition_crosswalk.yaml` | CallDisposition ↔ LMS family map | create |
| `app/services/enrichment/crosswalk.py` | load + apply crosswalk | create |
| `app/services/factories.py` | `make_call_analyzer()` | modify |
| `app/core/config.py` | `pipeline_mode` setting | modify |
| `app/workers/tasks.py` | `v2t.analyze` task + persist shim + handoff | modify |
| `app/scripts/ingest_crux_slice.py` | drive slice ingest from `duration_index.csv` | create |

---

### Task 1: Phone normalization parity (`app/utils/phone.py`)

**Files:**
- Create: `app/utils/phone.py`
- Test: `app/tests/unit/test_phone.py`

**Interfaces:**
- Produces: `normalize_mobile(value: object) -> str | None`, `clean_na(value: object) -> str | None`.

- [ ] **Step 1: Write the failing test** (port CI `tests/test_normalize.py` verbatim)

```python
# app/tests/unit/test_phone.py
from app.utils.phone import normalize_mobile, clean_na


class TestNormalizeMobile:
    def test_plain_valid_ten_digit_unchanged(self):
        assert normalize_mobile("9876543210") == "9876543210"

    def test_strips_91_country_code(self):
        assert normalize_mobile("919876543210") == "9876543210"

    def test_strips_leading_zero(self):
        assert normalize_mobile("09876543210") == "9876543210"

    def test_strips_plus_91_and_punctuation(self):
        assert normalize_mobile("+91 98765-43210") == "9876543210"

    def test_handles_float_formatted_number(self):
        assert normalize_mobile("9876543210.0") == "9876543210"

    def test_handles_actual_float_input(self):
        assert normalize_mobile(9876543210.0) == "9876543210"

    def test_nine_digit_lost_leading_zero_is_invalid(self):
        assert normalize_mobile("987654321") is None

    def test_starts_with_one_is_invalid(self):
        assert normalize_mobile("1234567890") is None

    def test_starts_with_five_is_invalid(self):
        assert normalize_mobile("5876543210") is None

    def test_na_string_is_none(self):
        assert normalize_mobile("NA") is None

    def test_empty_string_is_none(self):
        assert normalize_mobile("") is None

    def test_none_input_is_none(self):
        assert normalize_mobile(None) is None

    def test_nan_input_is_none(self):
        assert normalize_mobile(float("nan")) is None

    def test_too_many_digits_after_strip_is_invalid(self):
        assert normalize_mobile("1234567890123") is None


class TestCleanNa:
    def test_literal_na_becomes_none(self):
        assert clean_na("NA") is None

    def test_lowercase_na_becomes_none(self):
        assert clean_na("na") is None

    def test_whitespace_only_becomes_none(self):
        assert clean_na("   ") is None

    def test_real_value_preserved_and_trimmed(self):
        assert clean_na("  Salaried  ") == "Salaried"

    def test_other_null_spellings_become_none(self):
        for spelling in ["None", "null", "NULL", "#N/A", "N/A", "nan", "-"]:
            assert clean_na(spelling) is None, spelling
```

- [ ] **Step 2: Run test, confirm failure**

`./.venv/Scripts/python -m pytest app/tests/unit/test_phone.py -q` → FAIL (ModuleNotFoundError: app.utils.phone)

- [ ] **Step 3: Implement** (verbatim port of CI `src/normalize.py`)

```python
# app/utils/phone.py
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
```

- [ ] **Step 4: Run test, confirm pass** — `./.venv/Scripts/python -m pytest app/tests/unit/test_phone.py -q` → all pass
- [ ] **Step 5: Commit** — `git add app/utils/phone.py app/tests/unit/test_phone.py && git commit -m "feat(phone): normalize_mobile parity with Campaign Intelligence join key"`

---

### Task 2: Retain the Crux call id at ingest (`app/utils/crux_id.py` + `ingest.py`)

**Files:**
- Create: `app/utils/crux_id.py`
- Test: `app/tests/unit/test_crux_id.py`
- Modify: `app/api/routes/ingest.py` (the `ingest_upload` handler, ~line 253)

**Interfaces:**
- Produces: `crux_call_id_from_name(name: str | None) -> str | None` (returns the digit-run stem of a `<id>.mp3` filename, else None).

- [ ] **Step 1: Failing test**

```python
# app/tests/unit/test_crux_id.py
from app.utils.crux_id import crux_call_id_from_name


def test_extracts_eight_digit_id():
    assert crux_call_id_from_name("25689211.mp3") == "25689211"

def test_extracts_from_path_like_name():
    assert crux_call_id_from_name("38199637.MP3") == "38199637"

def test_non_numeric_stem_is_none():
    assert crux_call_id_from_name("upload.mp3") is None

def test_none_is_none():
    assert crux_call_id_from_name(None) is None

def test_empty_is_none():
    assert crux_call_id_from_name("") is None

def test_mixed_stem_is_none():
    assert crux_call_id_from_name("call_25689211_v2.mp3") is None
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement**

```python
# app/utils/crux_id.py
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
```

- [ ] **Step 4: Run, confirm pass**
- [ ] **Step 5: Wire into the upload handler.** In `app/api/routes/ingest.py`, replace the `metadata = CallMetadata(...)` block (~line 253) with:

```python
        crux_id = crux_call_id_from_name(safe_name)
        metadata = CallMetadata(
            campaign=campaign,
            channel=channel,
            stt_provider=stt_provider,
            extra={"crux_call_id": crux_id} if crux_id else {},
        )
```

and add the import near the top (with the other `app...` imports):

```python
from app.utils.crux_id import crux_call_id_from_name
```

- [ ] **Step 6: Commit** — `git add app/utils/crux_id.py app/tests/unit/test_crux_id.py app/api/routes/ingest.py && git commit -m "feat(ingest): retain Crux call_id in call metadata"`

---

### Task 3: New enums (`app/models/enums.py`)

**Files:**
- Modify: `app/models/enums.py`
- Test: `app/tests/unit/test_call_enums.py`

**Interfaces:**
- Produces: `CallDisposition`, `SentimentLabel` (StrEnum); `CallStatus.ANALYSIS_RUNNING`, `CallStatus.ANALYSIS_DONE`.

- [ ] **Step 1: Failing test**

```python
# app/tests/unit/test_call_enums.py
from app.models.enums import CallDisposition, SentimentLabel, CallStatus


def test_call_disposition_values():
    vals = {d.value for d in CallDisposition}
    assert {"resolved", "callback_requested", "not_interested", "wrong_number",
            "dnd", "no_response", "other"} <= vals

def test_sentiment_values():
    assert {s.value for s in SentimentLabel} == {"positive", "neutral", "negative"}

def test_new_call_statuses_exist():
    assert CallStatus.ANALYSIS_RUNNING.value == "analysis_running"
    assert CallStatus.ANALYSIS_DONE.value == "analysis_done"
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** — append to `app/models/enums.py`:

```python
class CallDisposition(StrEnum):
    """Outcome of an inbound service call (separate axis from the LMS DISPOSITION)."""

    RESOLVED = "resolved"
    INFO_PROVIDED = "info_provided"
    CALLBACK_REQUESTED = "callback_requested"
    FOLLOW_UP_PAYMENT = "follow_up_payment"
    COMPLAINT = "complaint"
    ESCALATION = "escalation"
    NOT_INTERESTED = "not_interested"
    NOT_ELIGIBLE = "not_eligible"
    SERVICE_REQUEST = "service_request"
    WRONG_NUMBER = "wrong_number"
    DND = "dnd"
    NO_RESPONSE = "no_response"
    OTHER = "other"


class SentimentLabel(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
```

and add two members to `CallStatus` (after `EXTRACTION_DONE`):

```python
    ANALYSIS_RUNNING = "analysis_running"
    ANALYSIS_DONE = "analysis_done"
```

- [ ] **Step 4: Run, confirm pass**
- [ ] **Step 5: Commit** — `git commit -am "feat(enums): add CallDisposition, SentimentLabel, analysis statuses"`

---

### Task 4: Pydantic schemas (`app/models/schemas.py`)

**Files:**
- Modify: `app/models/schemas.py`
- Test: `app/tests/unit/test_call_analysis_schemas.py`

**Interfaces:**
- Consumes: `CallDisposition`, `SentimentLabel`, `Language` from `app.models.enums`.
- Produces: `Lead`, `CallAnalysis`, `CallAnalysisResult` Pydantic models.

- [ ] **Step 1: Failing test**

```python
# app/tests/unit/test_call_analysis_schemas.py
from uuid import uuid4
from app.models.schemas import Lead, CallAnalysis, CallAnalysisResult
from app.models.enums import CallDisposition, SentimentLabel


def test_lead_all_optional():
    lead = Lead()
    assert lead.phone is None and lead.full_name is None

def test_call_analysis_minimal():
    a = CallAnalysis(
        disposition=CallDisposition.RESOLVED,
        sentiment=SentimentLabel.NEUTRAL,
    )
    assert a.escalation is False
    assert a.lead.phone is None

def test_result_wraps_analysis():
    cid = uuid4()
    res = CallAnalysisResult(
        call_id=cid,
        analysis=CallAnalysis(disposition=CallDisposition.OTHER, sentiment=SentimentLabel.NEUTRAL),
        used_model="openai/gpt-oss-120b",
    )
    assert res.call_id == cid
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** — add to `app/models/schemas.py` (import `CallDisposition, SentimentLabel` from enums):

```python
class Lead(BaseModel):
    """Lead attributes distilled from a call. Every field optional/grounded."""

    full_name: str | None = None
    phone: str | None = Field(default=None, description="Normalized 10-digit mobile (join key).")
    email: str | None = None
    age: int | None = None
    gender: str | None = None
    occupation: str | None = None
    education: str | None = None
    income_band: str | None = None
    pincode: str | None = None
    product_interest: str | None = None
    policy_no: str | None = None
    callback_time: str | None = None
    grounded_fields: list[str] = Field(default_factory=list)


class CallAnalysis(BaseModel):
    lead: Lead = Field(default_factory=Lead)
    disposition: CallDisposition = CallDisposition.OTHER
    disposition_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    disposition_rationale: str | None = None
    sentiment: SentimentLabel = SentimentLabel.NEUTRAL
    sentiment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    escalation: bool = False


class CallAnalysisResult(BaseModel):
    call_id: UUID
    analysis: CallAnalysis
    used_model: str
    raw_response: str | None = None
```

- [ ] **Step 4: Run, confirm pass**
- [ ] **Step 5: Commit** — `git commit -am "feat(schemas): Lead, CallAnalysis, CallAnalysisResult"`

---

### Task 5: Prompts + strict JSON schema (`app/prompts/call_analysis.py`, `call_analysis_schema.py`)

**Files:**
- Create: `app/prompts/call_analysis.py`, `app/prompts/call_analysis_schema.py`
- Test: `app/tests/unit/test_call_analysis_prompt.py`

**Interfaces:**
- Consumes: `CallDisposition`, `SentimentLabel` (enum value lists), `build_transcript_block` (reuse from `app.prompts.extraction`).
- Produces: `CALL_ANALYSIS_SYSTEM: str`, `CALL_ANALYSIS_USER_TEMPLATE: str`, `CALL_ANALYSIS_SCHEMA: dict`.

- [ ] **Step 1: Failing test**

```python
# app/tests/unit/test_call_analysis_prompt.py
from app.prompts.call_analysis_schema import CALL_ANALYSIS_SCHEMA
from app.prompts.call_analysis import CALL_ANALYSIS_SYSTEM, CALL_ANALYSIS_USER_TEMPLATE
from app.models.enums import CallDisposition, SentimentLabel


def test_schema_is_strict_object_with_required_top_level():
    props = CALL_ANALYSIS_SCHEMA["properties"]
    assert set(["lead", "disposition", "sentiment", "escalation"]) <= set(props)
    assert CALL_ANALYSIS_SCHEMA["additionalProperties"] is False

def test_disposition_enum_matches_enum():
    assert set(CALL_ANALYSIS_SCHEMA["properties"]["disposition"]["enum"]) == {d.value for d in CallDisposition}

def test_sentiment_enum_matches_enum():
    assert set(CALL_ANALYSIS_SCHEMA["properties"]["sentiment"]["enum"]) == {s.value for s in SentimentLabel}

def test_user_template_has_transcript_placeholder():
    assert "{transcript}" in CALL_ANALYSIS_USER_TEMPLATE
    assert "CUSTOMER" in CALL_ANALYSIS_SYSTEM or "customer" in CALL_ANALYSIS_SYSTEM
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement schema** (`call_analysis_schema.py`):

```python
"""Strict JSON schema for the per-call analysis LLM call. Mirrors CallAnalysis."""
from __future__ import annotations

from app.models.enums import CallDisposition, SentimentLabel

_DISPOSITIONS = [d.value for d in CallDisposition]
_SENTIMENTS = [s.value for s in SentimentLabel]

_LEAD_PROPS = {
    "full_name": {"type": ["string", "null"]},
    "phone": {"type": ["string", "null"]},
    "email": {"type": ["string", "null"]},
    "age": {"type": ["integer", "null"]},
    "gender": {"type": ["string", "null"]},
    "occupation": {"type": ["string", "null"]},
    "education": {"type": ["string", "null"]},
    "income_band": {"type": ["string", "null"]},
    "pincode": {"type": ["string", "null"]},
    "product_interest": {"type": ["string", "null"]},
    "policy_no": {"type": ["string", "null"]},
    "callback_time": {"type": ["string", "null"]},
}

CALL_ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lead", "disposition", "disposition_confidence", "sentiment",
                 "sentiment_confidence", "escalation"],
    "properties": {
        "lead": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_LEAD_PROPS.keys()),
            "properties": _LEAD_PROPS,
        },
        "disposition": {"type": "string", "enum": _DISPOSITIONS},
        "disposition_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "disposition_rationale": {"type": ["string", "null"]},
        "sentiment": {"type": "string", "enum": _SENTIMENTS},
        "sentiment_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "escalation": {"type": "boolean"},
    },
}

# disposition_rationale is optional content-wise but strict mode needs it required:
CALL_ANALYSIS_SCHEMA["required"].append("disposition_rationale")

__all__ = ["CALL_ANALYSIS_SCHEMA"]
```

- [ ] **Step 4: Implement prompt** (`call_analysis.py`):

```python
"""Per-call analysis prompt: lead info + disposition + sentiment in ONE pass over
the WHOLE transcript (both speakers, unlike the customer-only question extractor)."""
from __future__ import annotations

from app.prompts.extraction import build_transcript_block  # reuse

CALL_ANALYSIS_SYSTEM = """\
You analyze a single Indian life-insurance INBOUND SERVICE call transcript with
speaker labels (AGENT / CUSTOMER). Read the ENTIRE conversation — both parties —
and return ONE JSON object describing the lead, the call disposition, and sentiment.

LEAD: extract any identifying / profile attributes that are actually stated in the
call (by either speaker). Use null for anything not clearly stated. Never invent.
Phone numbers, names, emails, policy numbers must be quoted VERBATIM as spoken
(we verify them against the transcript). For `phone`, output the raw digits as
spoken; downstream normalizes to a 10-digit mobile.

DISPOSITION (pick exactly one): resolved, info_provided, callback_requested,
follow_up_payment, complaint, escalation, not_interested, not_eligible,
service_request, wrong_number, dnd, no_response, other.
Give a one-line `disposition_rationale` grounded in the call.

SENTIMENT: overall customer sentiment — positive, neutral, or negative.
`escalation` = true if the customer demanded a supervisor / threatened to leave /
raised a serious grievance.

Output STRICT JSON only. No prose, no markdown fences.
"""

CALL_ANALYSIS_USER_TEMPLATE = """\
Transcript (speaker-labeled):

{transcript}

Return one JSON object with keys: lead, disposition, disposition_confidence,
disposition_rationale, sentiment, sentiment_confidence, escalation.
"""

__all__ = ["CALL_ANALYSIS_SYSTEM", "CALL_ANALYSIS_USER_TEMPLATE", "build_transcript_block"]
```

- [ ] **Step 5: Run tests, confirm pass; Commit** — `git add app/prompts/call_analysis.py app/prompts/call_analysis_schema.py app/tests/unit/test_call_analysis_prompt.py && git commit -m "feat(prompts): call-analysis prompt + strict schema"`

---

### Task 6: CallAnalyzer service (`app/services/extraction/call_analysis.py`)

**Files:**
- Create: `app/services/extraction/call_analysis.py`
- Test: `app/tests/unit/test_call_analyzer.py`

**Interfaces:**
- Consumes: `GroqClient.chat_json`, `UtteranceSchema`, `detect_dominant_language`, `system_prompt_for` is NOT reused (analysis has its own system prompt — prepend the same `_OVERLAYS`? Keep simple: prepend nothing, the analysis prompt is language-agnostic). `CALL_ANALYSIS_SCHEMA`, `CALL_ANALYSIS_SYSTEM`, `CALL_ANALYSIS_USER_TEMPLATE`, `build_transcript_block`, `CallAnalysis`, `CallAnalysisResult`, `Lead`.
- Produces: `class CallAnalyzer` with `async def analyze(self, call_id: UUID, utterances: list[UtteranceSchema]) -> CallAnalysisResult`.

**Behavior:** one `chat_json` pass over the whole transcript (all speakers). On empty utterances, return a default `CallAnalysisResult` without calling the LLM. Validate the payload into `CallAnalysis` (Pydantic; on `ValidationError`, log + return a default analysis, never crash). **Grounding:** for each non-null `lead` string field among {full_name, phone, email, policy_no}, keep it only if it appears (digits-only compare for phone) in some utterance; otherwise null it and drop from `grounded_fields`. Populate `lead.grounded_fields` with the surviving grounded keys. For transcripts longer than ~12k chars, analyze the FIRST chunk only for disposition/sentiment but scan ALL utterances for lead grounding (lead facts may appear anywhere) — keep it simple: one pass on the full block truncated to a generous budget; note the truncation in logs.

- [ ] **Step 1: Failing test** (respx-mocked, mirrors `test_extractor.py`)

```python
# app/tests/unit/test_call_analyzer.py
from __future__ import annotations
import json
from uuid import uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.models.enums import Language, Speaker, CallDisposition, SentimentLabel
from app.models.schemas import UtteranceSchema
from app.services.extraction.call_analysis import CallAnalyzer
from app.services.llm.groq_client import GroqClient


def _utt(call_id, text, speaker=Speaker.CUSTOMER):
    return UtteranceSchema(id=uuid4(), call_id=call_id, speaker=speaker,
                           start_ts=0.0, end_ts=1.0, text=text,
                           language=Language.ENGLISH, confidence=0.9)


def _payload(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0,
            "model": get_settings().llm_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


@pytest.mark.asyncio
async def test_analyzer_extracts_grounded_lead_and_disposition():
    call_id = uuid4()
    utts = [
        _utt(call_id, "Hello, my name is Rahul Sharma and my number is 9876543210."),
        _utt(call_id, "Thank you Rahul, how can I help?", speaker=Speaker.AGENT),
        _utt(call_id, "I want to know my policy maturity date."),
    ]
    body = json.dumps({
        "lead": {"full_name": "Rahul Sharma", "phone": "9876543210", "email": None,
                 "age": None, "gender": None, "occupation": None, "education": None,
                 "income_band": None, "pincode": None, "product_interest": "maturity",
                 "policy_no": None, "callback_time": None},
        "disposition": "info_provided", "disposition_confidence": 0.8,
        "disposition_rationale": "Agent answered the maturity query.",
        "sentiment": "neutral", "sentiment_confidence": 0.7, "escalation": False,
    })
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        r.post("/chat/completions").mock(return_value=httpx.Response(200, json=_payload(body)))
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, utts)
        await client.aclose()

    assert res.call_id == call_id
    assert res.analysis.disposition == CallDisposition.INFO_PROVIDED
    assert res.analysis.sentiment == SentimentLabel.NEUTRAL
    assert res.analysis.lead.full_name == "Rahul Sharma"     # grounded
    assert res.analysis.lead.phone == "9876543210"           # grounded (digits present)
    assert "phone" in res.analysis.lead.grounded_fields


@pytest.mark.asyncio
async def test_analyzer_nulls_ungrounded_phone():
    call_id = uuid4()
    utts = [_utt(call_id, "I just want my premium receipt please.")]
    body = json.dumps({
        "lead": {"full_name": None, "phone": "9999999999", "email": None, "age": None,
                 "gender": None, "occupation": None, "education": None, "income_band": None,
                 "pincode": None, "product_interest": None, "policy_no": None, "callback_time": None},
        "disposition": "service_request", "disposition_confidence": 0.6,
        "disposition_rationale": "Asked for receipt.",
        "sentiment": "neutral", "sentiment_confidence": 0.5, "escalation": False,
    })
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        r.post("/chat/completions").mock(return_value=httpx.Response(200, json=_payload(body)))
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, utts)
        await client.aclose()

    assert res.analysis.lead.phone is None        # 9999999999 not in transcript -> nulled
    assert "phone" not in res.analysis.lead.grounded_fields


@pytest.mark.asyncio
async def test_analyzer_empty_utterances_short_circuits():
    call_id = uuid4()
    settings = get_settings()
    with respx.mock(base_url=settings.llm_base_url.rstrip("/"), assert_all_called=False) as r:
        route = r.post("/chat/completions")
        client = GroqClient()
        res = await CallAnalyzer(client).analyze(call_id, [])
        await client.aclose()
    assert route.called is False
    assert res.call_id == call_id
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** the service:

```python
# app/services/extraction/call_analysis.py
"""One-pass per-call analyzer: lead + disposition + sentiment over the WHOLE
transcript (both speakers). Clones the LLMExtractor scaffolding (chat_json strict
schema, Pydantic validate-and-skip, grounding guard for PII)."""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from pydantic import ValidationError

from app.models.schemas import CallAnalysis, CallAnalysisResult, Lead, UtteranceSchema
from app.prompts.call_analysis import (
    CALL_ANALYSIS_SYSTEM,
    CALL_ANALYSIS_USER_TEMPLATE,
    build_transcript_block,
)
from app.prompts.call_analysis_schema import CALL_ANALYSIS_SCHEMA
from app.services.llm.groq_client import GroqClient

logger = structlog.get_logger(__name__)

_CHAR_BUDGET = 14_000
_GROUNDED_STRING_FIELDS = ("full_name", "email", "policy_no")  # phone handled separately
_DIGITS = re.compile(r"\D")


class CallAnalyzer:
    def __init__(self, client: GroqClient) -> None:
        self._client = client

    async def analyze(
        self, call_id: UUID, utterances: list[UtteranceSchema]
    ) -> CallAnalysisResult:
        if not utterances:
            return CallAnalysisResult(
                call_id=call_id,
                analysis=CallAnalysis(),
                used_model=self._client.model,
                raw_response=None,
            )

        block = build_transcript_block(
            [{"speaker": u.speaker.value, "text": u.text, "start_ts": u.start_ts}
             for u in utterances]
        )[:_CHAR_BUDGET]
        user = CALL_ANALYSIS_USER_TEMPLATE.format(transcript=block)

        try:
            payload = await self._client.chat_json(
                system=CALL_ANALYSIS_SYSTEM,
                user=user,
                json_schema=CALL_ANALYSIS_SCHEMA,
                schema_name="call_analysis",
            )
        except Exception as exc:  # noqa: BLE001 — retries exhausted
            logger.error("analyzer.llm_failed", call_id=str(call_id), error=str(exc))
            return CallAnalysisResult(call_id=call_id, analysis=CallAnalysis(),
                                      used_model=self._client.model, raw_response=None)

        try:
            analysis = CallAnalysis.model_validate(payload)
        except ValidationError as exc:
            logger.warning("analyzer.invalid_payload", call_id=str(call_id),
                           error=exc.errors(include_url=False)[:3])
            analysis = CallAnalysis()

        analysis.lead = _ground_lead(analysis.lead, utterances)
        return CallAnalysisResult(
            call_id=call_id, analysis=analysis,
            used_model=self._client.model, raw_response=_safe(payload),
        )


def _ground_lead(lead: Lead, utterances: list[UtteranceSchema]) -> Lead:
    joined = " ".join(u.text for u in utterances).lower()
    joined_digits = _DIGITS.sub("", joined)
    grounded: list[str] = []

    for field in _GROUNDED_STRING_FIELDS:
        val = getattr(lead, field)
        if val and str(val).lower() in joined:
            grounded.append(field)
        elif val:
            setattr(lead, field, None)

    if lead.phone:
        pdigits = _DIGITS.sub("", str(lead.phone))
        if pdigits and pdigits in joined_digits:
            grounded.append("phone")
        else:
            lead.phone = None

    lead.grounded_fields = grounded
    return lead


def _safe(payload: dict[str, Any]) -> str:
    try:
        import orjson
        return orjson.dumps(payload).decode("utf-8")
    except Exception:  # noqa: BLE001
        return repr(payload)


__all__ = ["CallAnalyzer"]
```

- [ ] **Step 4: Run, confirm pass**
- [ ] **Step 5: Commit** — `git add app/services/extraction/call_analysis.py app/tests/unit/test_call_analyzer.py && git commit -m "feat(analyzer): one-pass CallAnalyzer with PII grounding"`

---

### Task 7: Disposition crosswalk (`app/data/disposition_crosswalk.yaml` + `app/services/enrichment/crosswalk.py`)

**Files:**
- Create: `app/data/disposition_crosswalk.yaml`, `app/services/enrichment/__init__.py`, `app/services/enrichment/crosswalk.py`
- Test: `app/tests/unit/test_crosswalk.py`

**Interfaces:**
- Produces: `load_crosswalk() -> dict[str, str]` (LMS-value-lower → CallDisposition value), `map_lms_disposition(value: str | None) -> CallDisposition`.

- [ ] **Step 1: Failing test**

```python
# app/tests/unit/test_crosswalk.py
from app.services.enrichment.crosswalk import load_crosswalk, map_lms_disposition
from app.models.enums import CallDisposition


def test_crosswalk_maps_known_lms_values():
    assert map_lms_disposition("Ringing") == CallDisposition.NO_RESPONSE
    assert map_lms_disposition("NW - DNC") == CallDisposition.DND
    assert map_lms_disposition("NI - Due to Price") == CallDisposition.NOT_INTERESTED
    assert map_lms_disposition("Meeting Scheduled") == CallDisposition.OTHER

def test_unknown_maps_to_other():
    assert map_lms_disposition("totally novel value") == CallDisposition.OTHER

def test_none_maps_to_other():
    assert map_lms_disposition(None) == CallDisposition.OTHER

def test_every_target_is_a_valid_enum():
    targets = set(load_crosswalk().values())
    assert targets <= {d.value for d in CallDisposition}
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement YAML** (`app/data/disposition_crosswalk.yaml`):

```yaml
# LMS free-text DISPOSITION (lower-cased) -> CallDisposition value.
# Seeded from the distinct values in Campaign Intelligence leads_canonical.csv.
"ringing": no_response
"call not connected/beep tone": no_response
"ivr tone": no_response
"short hang up": no_response
"call back - ringing": no_response
"call back later": callback_requested
"buy later": callback_requested
"just browsing": not_interested
"ni - due to service": not_interested
"ni - due to product": not_interested
"ni - due to price": not_interested
"ni - need offline policy": not_interested
"ne - ne due to income": not_eligible
"ne - ne due to documents not avaible(education)": not_eligible
"ne - ne due to documents not avaible": not_eligible
"ne - ne due to medical history": not_eligible
"nw - incorrect request": wrong_number
"incorrect request": wrong_number
"nw - never left name and number": wrong_number
"never left name and number": wrong_number
"nw - dnc": dnd
"nw - service request": service_request
"nw - duplicate sale closed(already assisted)": service_request
"follow up for payment": follow_up_payment
"meeting scheduled": other
"looking for other product(health or loan)": not_interested
"loan requirement": not_interested
```

- [ ] **Step 4: Implement loader** (`app/services/enrichment/crosswalk.py`):

```python
"""Map free-text LMS dispositions to the controlled CallDisposition enum."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.models.enums import CallDisposition

_PATH = Path(__file__).resolve().parents[2] / "data" / "disposition_crosswalk.yaml"


@lru_cache(maxsize=1)
def load_crosswalk() -> dict[str, str]:
    with _PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def map_lms_disposition(value: str | None) -> CallDisposition:
    if not value:
        return CallDisposition.OTHER
    target = load_crosswalk().get(str(value).strip().lower())
    if target and target in CallDisposition._value2member_map_:
        return CallDisposition(target)
    return CallDisposition.OTHER


__all__ = ["load_crosswalk", "map_lms_disposition"]
```

(If `pyyaml` is not installed, add it: confirm with `./.venv/Scripts/python -c "import yaml"`. It is a transitive dep of many tools; if missing, `./.venv/Scripts/python -m pip install pyyaml`.)

- [ ] **Step 5: Run, confirm pass; Commit** — `git add app/data/disposition_crosswalk.yaml app/services/enrichment/ app/tests/unit/test_crosswalk.py && git commit -m "feat(crosswalk): LMS disposition -> CallDisposition map"`

---

### Task 8: Factory + config + `v2t.analyze` task + handoff wiring

**Files:**
- Modify: `app/services/factories.py` (add `make_call_analyzer`), `app/core/config.py` (`pipeline_mode`), `app/workers/tasks.py` (task + persist shim + handoff)
- Test: `app/tests/unit/test_analyze_persist.py`

**Interfaces:**
- Consumes: `CallAnalyzer`, `get_llm_client`, `CallStatus.ANALYSIS_*`, `_load_utterances`, `run_async`, `set_call_status`.
- Produces: factory `make_call_analyzer() -> CallAnalyzer`; Celery task `v2t.analyze`; `_persist_call_analysis(session, call_id, result)`; `_first_analysis_stage(mode) -> str`.

- [ ] **Step 1: Add `pipeline_mode` to `app/core/config.py`** (after the extraction setting):

```python
    # ---- Pipeline mode ----
    # "lead": transcribe -> analyze (lead/disposition/sentiment), skip the FAQ
    # clustering tail. "faq": legacy customer-question + clustering pipeline.
    # "both": run analyze then continue into extraction/clustering.
    pipeline_mode: Literal["lead", "faq", "both"] = "lead"
```

- [ ] **Step 2: Add factory to `app/services/factories.py`:**

```python
from app.services.extraction.call_analysis import CallAnalyzer  # at top with imports

def make_call_analyzer() -> CallAnalyzer:
    return CallAnalyzer(get_llm_client())
```
(and add `"make_call_analyzer"` to `__all__`.)

- [ ] **Step 3: Write the persist-shim test** (no DB — assert the row dict the shim builds; refactor the shim to build a dict via a pure helper `_call_analysis_metadata(result)`):

```python
# app/tests/unit/test_analyze_persist.py
from uuid import uuid4
from app.models.schemas import CallAnalysis, CallAnalysisResult, Lead
from app.models.enums import CallDisposition, SentimentLabel
from app.workers.tasks import _call_analysis_metadata


def test_metadata_shape():
    res = CallAnalysisResult(
        call_id=uuid4(),
        analysis=CallAnalysis(
            lead=Lead(phone="9876543210", full_name="A B", grounded_fields=["phone"]),
            disposition=CallDisposition.RESOLVED, disposition_confidence=0.9,
            sentiment=SentimentLabel.POSITIVE, sentiment_confidence=0.8, escalation=False,
        ),
        used_model="m",
    )
    meta = _call_analysis_metadata(res)
    assert meta["disposition"] == "resolved"
    assert meta["sentiment"] == "positive"
    assert meta["lead"]["phone"] == "9876543210"
    assert meta["model"] == "m"
```

- [ ] **Step 4: Run, confirm failure**
- [ ] **Step 5: Implement in `app/workers/tasks.py`** — add the pure helper, the persist shim, the stage selector, and the task:

```python
def _call_analysis_metadata(result) -> dict:
    a = result.analysis
    return {
        "lead": a.lead.model_dump(),
        "disposition": a.disposition.value,
        "disposition_confidence": a.disposition_confidence,
        "disposition_rationale": a.disposition_rationale,
        "sentiment": a.sentiment.value,
        "sentiment_confidence": a.sentiment_confidence,
        "escalation": a.escalation,
        "model": result.used_model,
    }


def _persist_call_analysis(session, call_id, result) -> None:
    from sqlalchemy import text
    session.execute(
        text(
            "UPDATE calls SET metadata = jsonb_set("
            "COALESCE(metadata, '{}'::jsonb), '{analysis}', :payload::jsonb, true) "
            "WHERE id = :cid"
        ),
        {"payload": __import__("json").dumps(_call_analysis_metadata(result)),
         "cid": str(call_id)},
    )


def _first_analysis_stage(mode: str) -> str:
    return "v2t.analyze" if mode in ("lead", "both") else "v2t.extract"


@celery_app.task(bind=True, name="v2t.analyze", acks_late=True)
def analyze_call(self, call_id: str) -> None:
    _bind(call_id, stage="analyze")
    log.info("analyze_start")
    try:
        with _stage("analyze"):
            with sync_session() as session:
                set_call_status(session, call_id, CallStatus.ANALYSIS_RUNNING.value)
                utterances = _load_utterances(session, call_id)
            from app.services.factories import make_call_analyzer
            analyzer = make_call_analyzer()
            result = run_async(analyzer.analyze(UUID(str(call_id)), utterances))
            with sync_session() as session:
                _persist_call_analysis(session, call_id, result)
                set_call_status(session, call_id, CallStatus.ANALYSIS_DONE.value)
        from app.core.config import get_settings
        if get_settings().pipeline_mode == "both":
            _next("v2t.extract", call_id)
        log.info("analyze_done", disposition=result.analysis.disposition.value)
    except Exception as exc:
        if _is_transient(exc):
            log.warning("analyze_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("analyze_failed")
        _mark_failed(call_id, str(exc))
        raise
```

- [ ] **Step 6: Re-point the transcribe/load_transcript handoff.** In `transcribe_call` and `load_transcript`, change `_next("v2t.extract", call_id)` to:

```python
            from app.core.config import get_settings
            _next(_first_analysis_stage(get_settings().pipeline_mode), call_id)
```
Add `"analyze_call"` to `__all__`.

- [ ] **Step 7: Run the persist test, confirm pass; Commit** — `git commit -am "feat(pipeline): v2t.analyze task + lead-mode handoff + pipeline_mode"`

---

### Task 9 (P2): Crux slice ingestion script (`app/scripts/ingest_crux_slice.py`)

**Files:**
- Create: `app/scripts/ingest_crux_slice.py`
- Test: `app/tests/unit/test_ingest_crux_slice.py` (test the pure slice-selection function only)

**Interfaces:**
- Produces: `select_slice(rows: list[dict], *, min_duration: float, buckets: set[str], limit: int, seed: int) -> list[dict]` and a `main()` that uploads via `/ingest/upload` (reuse the `ingest_dataset_sample.py` uploader pattern, passing `channel="inbound"`).

- [ ] **Step 1: Failing test** for the pure selector:

```python
# app/tests/unit/test_ingest_crux_slice.py
from app.scripts.ingest_crux_slice import select_slice


def _row(fn, dur, bucket):
    return {"filename": fn, "duration_sec": dur, "bucket": bucket}

def test_filters_by_duration_and_bucket_and_limit():
    rows = [_row(f"{i}.mp3", float(i), "15-30s" if i >= 15 else "under_15s") for i in range(10, 40)]
    sel = select_slice(rows, min_duration=30.0, buckets={"15-30s", "30s+"}, limit=3, seed=1)
    assert len(sel) == 3
    assert all(float(r["duration_sec"]) >= 30.0 for r in sel)
    assert all(r["bucket"] in {"15-30s", "30s+"} for r in sel)

def test_deterministic_for_seed():
    rows = [_row(f"{i}.mp3", 60.0, "30s+") for i in range(100)]
    a = select_slice(rows, min_duration=0.0, buckets={"30s+"}, limit=5, seed=42)
    b = select_slice(rows, min_duration=0.0, buckets={"30s+"}, limit=5, seed=42)
    assert [r["filename"] for r in a] == [r["filename"] for r in b]
```

- [ ] **Step 2: Run, confirm failure**
- [ ] **Step 3: Implement** `select_slice` (pure) + a `main()` that reads `duration_index.csv`, runs `select_slice`, and uploads the chosen mp3s via `/ingest/upload` with `data={"channel": "inbound"}` (clone the async uploader from `app/scripts/ingest_dataset_sample.py`; resolve each file from its `original_path`/`new_path`). Keep `select_slice` importable without network.

```python
# core selector (full main() mirrors ingest_dataset_sample.py uploader)
import random

def select_slice(rows, *, min_duration, buckets, limit, seed):
    elig = [r for r in rows
            if float(r.get("duration_sec") or 0) >= min_duration
            and (r.get("bucket") in buckets if buckets else True)]
    rng = random.Random(seed)
    return elig if len(elig) <= limit else rng.sample(elig, limit)
```

- [ ] **Step 4: Run, confirm pass; Commit** — `git add app/scripts/ingest_crux_slice.py app/tests/unit/test_ingest_crux_slice.py && git commit -m "feat(scripts): crux slice selection + ingestion"`

---

## Self-Review

- **Spec coverage:** Task 1 → §5.3 join parity; Task 2 → §5.1 + §7 (crux id retention); Tasks 3–6 → §5.4/§5.5 (analyzer + enums + schema); Task 7 → §8 crosswalk; Task 8 → §5.7 pipeline wiring + §5.12 (lead-mode skips FAQ tail); Task 9 → §5.11 + §10 slice. The full enrichment join (§5.8), graph (§5.9–5.10), CDR loader (§5.2), and DB-table promotion (§6/P3) are intentionally OUT of this P0–P2 plan and get their own plans.
- **Placeholder scan:** none — every code step has concrete content.
- **Type consistency:** `CallAnalysisResult.analysis: CallAnalysis`, `CallAnalysis.lead: Lead`, analyzer returns `CallAnalysisResult`, task consumes `result.analysis.*` and `_call_analysis_metadata(result)`. `_first_analysis_stage(mode)` used by both handoff sites and the task. Consistent.
- **Validation gate (P2):** after Task 9, run the slice, dump `calls.metadata.analysis`, hand-label ~100, and measure lead/phone grounding + disposition accuracy before any corpus-scale ingestion (separate, manual step — not automated here).
