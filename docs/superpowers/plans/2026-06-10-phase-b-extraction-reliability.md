# Phase B — Extraction Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LLM question extraction trustworthy (no silently-defaulted intents, no hallucinated/ungrounded questions, Urdu-language coverage), fix two cluster-integrity bugs, then re-run the pipeline and Phase A diagnostics on clean data.

**Architecture:** All fixes are small, surgical changes to existing modules — the extractor coercion layer (`app/services/extraction/llm_extractor.py`), the language-overlay module (`app/prompts/extraction_lang.py`), the batch-cluster persistence (`app/services/factories.py`), and the canonical-FAQ persistence (`app/workers/tasks.py`). A new operational script re-enqueues extraction. No schema changes, no new tables.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy Core (text SQL), Celery, pytest (asyncio_mode=auto), Ollama/Gemma, Postgres+pgvector.

**Why (context for an engineer with zero history):** Phase A Task 4 (see `docs/superpowers/diagnostics/2026-06-10-task4-interpretation.md`) found that of 16 extracted questions in the live DB, 13 were hallucinated by the LLM (their `raw_text` appears in no utterance) and carried Pydantic-defaulted `intent='other'`, `confidence=0.0`, `english_gloss=NULL` because Gemma omitted those fields and nothing noticed. 17 of 24 zero-question calls are Urdu (Arabic script), a language the prompt-overlay layer doesn't handle. Two clusters have wrong `frequency` and NULL `label`/`canonical_question`. Until these are fixed, clustering-granularity tuning (the original Phase B) is measuring garbage.

---

## Environment / how to run anything on this machine (macOS, no local 3.11 venv)

System Python is 3.10; the app needs 3.11 (`StrEnum`). **Every** pytest/script run goes through the api image with the local code mounted:

```bash
# Run unit tests (substitute the test path):
docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" \
  --entrypoint python api -m pytest app/tests/unit/test_extractor_coercion.py -v

# Run a script that needs the DB (drop --no-deps so postgres dependency resolves; it's already up):
docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" -v "$(pwd)/docs:/app/docs" \
  --entrypoint python api -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300
```

Do NOT `docker exec` long scripts inside the running `v2t-api` container — its restart policy has killed one mid-run. Commit after every task.

---

### Task 1: Detect and log LLM-omitted classification fields (`intent`/`confidence`/`english_gloss`)

The LLM sometimes returns question objects missing `intent`, `confidence`, and `english_gloss`. `ExtractedQuestion` (app/models/schemas.py:117-123) silently fills defaults (`other`/`0.0`/`None`). We keep the rows (a usable question without an intent is still a question) but must *see* it happening: a structured WARNING log + a `degraded` metric status.

**Files:**
- Modify: `app/services/extraction/llm_extractor.py` (inside `_coerce_questions`, after the alias-repair block at lines 189-202)
- Test: `app/tests/unit/test_extractor_coercion.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `app/tests/unit/test_extractor_coercion.py`:

```python
def test_missing_intent_and_confidence_logs_degraded(caplog) -> None:
    """Items missing intent/confidence/english_gloss are KEPT but logged."""
    import logging

    call_id = uuid4()
    payload = {
        "questions": [
            {"raw_text": "What is the claim process?", "language": "en"}
        ]
    }

    with caplog.at_level(logging.WARNING):
        out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1  # still kept
    assert out[0].intent.value == "other"  # pydantic default applied
    assert any("fields_defaulted" in r.message for r in caplog.records)


def test_complete_item_does_not_log_degraded(caplog) -> None:
    import logging

    call_id = uuid4()
    payload = {
        "questions": [
            {
                "raw_text": "claim kaise karein?",
                "normalized_text": "How do I file a claim?",
                "english_gloss": "How do I file a claim?",
                "question_type": "question",
                "intent": "claim_process",
                "secondary_intents": [],
                "language": "hi-en",
                "confidence": 0.9,
            }
        ]
    }

    with caplog.at_level(logging.WARNING):
        out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
    assert not any("fields_defaulted" in r.message for r in caplog.records)
```

Note: `structlog` is configured in tests to pass through stdlib logging, so `caplog` sees the event name in `r.message`. If the first run shows `caplog.records` empty even after implementation, switch the assertion to capturing via `structlog.testing.capture_logs()` instead — but try `caplog` first since other tests in this repo use plain asserts.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extractor_coercion.py -v`
Expected: the 4 existing tests PASS, `test_missing_intent_and_confidence_logs_degraded` FAILS (no `fields_defaulted` record).

- [ ] **Step 3: Implement the detection**

In `app/services/extraction/llm_extractor.py`, inside `_coerce_questions`, insert AFTER the alias-repair/language block (after the `if not item.get("language"):` block ending around line 202) and BEFORE the `item.setdefault("call_id", ...)` stamping:

```python
        # Gemma frequently omits the classification fields entirely; Pydantic
        # then fills intent=other / confidence=0.0 / gloss=None silently.
        # Keep the row but make the degradation observable.
        _DEFAULTED_FIELDS = ("intent", "confidence", "english_gloss")
        missing_fields = [
            f for f in _DEFAULTED_FIELDS if item.get(f) in (None, "")
        ]
        if missing_fields:
            logger.warning(
                "extractor.fields_defaulted",
                call_id=str(call_id),
                missing=missing_fields,
                raw_text=(item.get("raw_text") or "")[:80],
            )
            extraction_processed.labels(status="degraded").inc()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extractor_coercion.py -v`
Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/extraction/llm_extractor.py app/tests/unit/test_extractor_coercion.py
git commit -m "feat(extraction): log + count LLM-omitted intent/confidence/gloss fields"
```

---

### Task 2: Drop ungrounded (hallucinated) questions, behind a config flag

13 of the 16 live questions match no utterance — the model invented them. `raw_text` is documented as an "Original customer utterance excerpt", so a genuine extraction substring-matches some utterance (`_match_utterance_id`, llm_extractor.py:232-255). Questions with no match get dropped — but ONLY when we actually had the chunk to check against, and only when the new setting is on (escape hatch for paraphrase-heavy models).

**Files:**
- Modify: `app/core/config.py` (Settings class, near the other extraction/llm settings around line 90)
- Modify: `app/services/extraction/llm_extractor.py` (in `_coerce_questions`, the utterance-match block at lines 222-227)
- Test: `app/tests/unit/test_extractor_coercion.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `app/tests/unit/test_extractor_coercion.py`:

```python
def _utterance(text: str):
    """Minimal UtteranceSchema for grounding tests."""
    from uuid import uuid4 as _uuid4

    from app.models.enums import Speaker
    from app.models.schemas import UtteranceSchema

    return UtteranceSchema(
        id=_uuid4(),
        call_id=_uuid4(),
        speaker=Speaker.CUSTOMER,
        start_ts=0.0,
        end_ts=1.0,
        text=text,
        language="en",
        confidence=1.0,
    )


def test_ungrounded_question_dropped_when_chunk_present() -> None:
    call_id = uuid4()
    chunk = [_utterance("Hello, I am calling about my policy renewal date.")]
    payload = {
        "questions": [
            {"raw_text": "What are the coverage options for critical illness?",
             "language": "en"}
        ]
    }

    out = _coerce_questions(payload, call_id, chunk)

    assert out == []  # hallucinated: matches no utterance


def test_grounded_question_kept() -> None:
    call_id = uuid4()
    chunk = [_utterance("Sir, what is the claim process for this policy?")]
    payload = {
        "questions": [
            {"raw_text": "what is the claim process", "language": "en"}
        ]
    }

    out = _coerce_questions(payload, call_id, chunk)

    assert len(out) == 1
    assert out[0].utterance_id == chunk[0].id


def test_empty_chunk_cannot_ground_so_question_is_kept() -> None:
    """Callers that pass no chunk (tests, future batch paths) keep questions."""
    call_id = uuid4()
    payload = {"questions": [{"raw_text": "Is this policy tax-deductible?",
                              "language": "en"}]}

    out = _coerce_questions(payload, call_id, [])

    assert len(out) == 1
```

Check `UtteranceSchema`'s required fields in `app/models/schemas.py` (top of file) before running — if field names differ (e.g. no `confidence`), adjust `_utterance` to satisfy the actual schema; the test's substance is the `text` and `id` fields.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extractor_coercion.py -v`
Expected: `test_ungrounded_question_dropped_when_chunk_present` FAILS (returns 1 item today); the other two PASS already (they describe current behaviour and pin it).

- [ ] **Step 3: Add the config setting**

In `app/core/config.py`, inside `class Settings`, next to `llm_model` (line 90):

```python
    # Drop extracted questions whose raw_text matches no utterance in the
    # chunk they came from (LLM hallucination guard). Escape hatch: set False
    # if a future model legitimately paraphrases raw_text.
    extraction_drop_ungrounded: bool = True
```

- [ ] **Step 4: Implement the drop**

In `app/services/extraction/llm_extractor.py`, replace the utterance-match block in `_coerce_questions` (currently lines 222-227):

```python
        # Best-effort utterance match by substring of raw_text.
        if question.utterance_id is None:
            question.utterance_id = _match_utterance_id(question.raw_text, chunk)

        # Hallucination guard: a real extraction's raw_text comes verbatim
        # from the call, so it must match some utterance. We can only judge
        # this when we have the chunk.
        if question.utterance_id is None and chunk:
            from app.core.config import get_settings

            logger.warning(
                "extractor.ungrounded_question",
                call_id=str(call_id),
                raw_text=question.raw_text[:80],
                dropped=get_settings().extraction_drop_ungrounded,
            )
            extraction_processed.labels(status="ungrounded").inc()
            if get_settings().extraction_drop_ungrounded:
                continue

        out.append(question)
        extraction_processed.labels(status="ok").inc()
```

(The final two lines already exist — the new code goes between the match and the append.)

- [ ] **Step 5: Run the full coercion test file**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extractor_coercion.py -v`
Expected: all 9 PASS (4 original + 2 from Task 1 + 3 new).

- [ ] **Step 6: Run the wider extractor tests for regressions**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extractor.py app/tests/unit/test_extraction_schema.py -v`
Expected: PASS. If `test_extractor.py` feeds chunks whose questions don't substring-match (now dropped), inspect: if the fixture questions are meant to be grounded, fix the fixture text to match; do NOT weaken the guard.

- [ ] **Step 7: Commit**

```bash
git add app/core/config.py app/services/extraction/llm_extractor.py app/tests/unit/test_extractor_coercion.py
git commit -m "feat(extraction): drop ungrounded (hallucinated) questions behind config flag"
```

---

### Task 3: Urdu / Arabic-script language bucket + prompt overlay

17 of 24 zero-question calls are Urdu in Arabic script. `detect_dominant_language` (app/prompts/extraction_lang.py:75-115) has no Arabic-script heuristic, so these fall to langid → `other`, whose overlay mentions "Tamil, Telugu" — useless context for Gemma on Urdu insurance calls.

**Files:**
- Modify: `app/prompts/extraction_lang.py`
- Test: Create `app/tests/unit/test_extraction_lang.py`

- [ ] **Step 1: Write the failing tests**

Create `app/tests/unit/test_extraction_lang.py`:

```python
"""Unit tests: dominant-language bucketing for prompt overlays."""

from __future__ import annotations

from app.prompts.extraction_lang import detect_dominant_language, system_prompt_for

URDU_SAMPLE = (
    "میں اپنی پالیسی کے بارے میں بات کرنا چاہتا ہوں۔ "
    "پریمیم کی رقم کتنی ہے؟ کلیم کا طریقہ کیا ہے؟ "
    "مجھے دستاویزات کہاں جمع کرانی ہوں گی؟ "
) * 5

DEVANAGARI_SAMPLE = (
    "मैं अपनी पॉलिसी के बारे में बात करना चाहता हूं। प्रीमियम कितना है? "
) * 5


def test_arabic_script_detected_as_ur() -> None:
    assert detect_dominant_language(URDU_SAMPLE) == "ur"


def test_devanagari_still_hi() -> None:
    assert detect_dominant_language(DEVANAGARI_SAMPLE) == "hi"


def test_english_still_en() -> None:
    text = "I want to ask about my policy premium and the claim process." * 5
    assert detect_dominant_language(text) == "en"


def test_ur_overlay_present_and_prepended() -> None:
    prompt = system_prompt_for("ur")
    assert "Urdu" in prompt
    assert "english_gloss" in prompt
```

- [ ] **Step 2: Run tests to verify failure**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extraction_lang.py -v`
Expected: `test_arabic_script_detected_as_ur` FAILS (returns "other"); `test_ur_overlay_present_and_prepended` FAILS (no "ur" overlay → bare prompt without "Urdu"). The hi/en tests PASS.

- [ ] **Step 3: Implement the `ur` bucket**

In `app/prompts/extraction_lang.py`:

(a) Widen the bucket type (line 27):

```python
Bucket = Literal["hi", "en", "ur", "other"]
```

(b) Add the overlay to `_OVERLAYS` (after the `"en"` entry):

```python
    "ur": (
        "LANGUAGE HINT: This call is primarily Urdu (Arabic script or Roman "
        "transliteration). Insurance terms appear as پالیسی (policy), "
        "پریمیم (premium), کلیم (claim). Customers phrase questions "
        "indirectly and politely. Keep `normalized_text` in the customer's "
        "script. `english_gloss` must ALWAYS be provided. Extract questions "
        "even when phrased as statements of confusion or requests.\n\n"
    ),
```

(c) In `detect_dominant_language`, add an Arabic-script heuristic directly after the Devanagari heuristic (after line 93):

```python
    # 1b. Arabic-script heuristic — Urdu calls transcribed in Arabic script.
    #     U+0600–U+06FF covers Arabic/Urdu letters incl. ٹ ڈ ے etc.
    arabic = sum(1 for ch in sample if "؀" <= ch <= "ۿ")
    if arabic >= max(20, len(sample) // 50):
        return "ur"
```

(d) In the langid branch (lines 96-105), map langid's Urdu/Arabic/Persian answers to the new bucket — replace `langid_bucket = "en" if lang == "en" else "other"` with:

```python
        if lang in ("ur", "ar", "fa"):
            return "ur"
        langid_bucket = "en" if lang == "en" else "other"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_extraction_lang.py app/tests/unit/test_extractor_coercion.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/prompts/extraction_lang.py app/tests/unit/test_extraction_lang.py
git commit -m "feat(extraction): Urdu/Arabic-script language bucket with prompt overlay"
```

---

### Task 4: Recount frequency when clusters are dissolved

`_persist_batch_async` (app/services/factories.py:298-362) flips dissolved clusters to `is_stable=FALSE` but never recounts `frequency`, leaving orphans like the live cluster with frequency=4 and 0 members.

**Files:**
- Modify: `app/services/factories.py:348-355` (the `dissolved` loop)
- Test: Create `app/tests/unit/test_persist_batch.py`

- [ ] **Step 1: Write the failing test**

Create `app/tests/unit/test_persist_batch.py`:

```python
"""Unit tests: batch-reconciliation persistence keeps frequency consistent."""

from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

from app.services import factories


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.statements.append((str(clause), params or {}))


async def test_dissolved_cluster_frequency_recounted(monkeypatch) -> None:
    recorder = _RecordingSession()

    @contextmanager
    def _fake_sync_session():
        yield recorder

    monkeypatch.setattr(factories, "sync_session", _fake_sync_session)

    cid = uuid4()
    await factories._persist_batch_async([], [], [cid])

    dissolved_sql = [
        sql for sql, _ in recorder.statements if "is_stable" in sql.lower()
    ]
    assert len(dissolved_sql) == 1
    # The same UPDATE must recount frequency from cluster_members.
    assert "frequency" in dissolved_sql[0].lower()
    assert "count(*)" in dissolved_sql[0].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_persist_batch.py -v`
Expected: FAIL — the current dissolved UPDATE has no `frequency`.

- [ ] **Step 3: Implement the recount**

In `app/services/factories.py`, replace the dissolved loop (lines 348-355):

```python
            for cid in dissolved or []:
                session.execute(
                    _sql(
                        """
                        UPDATE semantic_clusters
                        SET is_stable = FALSE,
                            frequency = (
                                SELECT COUNT(*) FROM cluster_members
                                WHERE cluster_id = semantic_clusters.id
                            ),
                            last_updated = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"id": str(cid)},
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_persist_batch.py app/tests/unit/test_clustering.py app/tests/unit/test_incremental.py -v`
Expected: PASS (including the existing clustering tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/factories.py app/tests/unit/test_persist_batch.py
git commit -m "fix(clustering): recount frequency when dissolving clusters"
```

---

### Task 5: Backfill `semantic_clusters.label` / `canonical_question` from canonical FAQs

`_persist_canonical_faq` (app/workers/tasks.py:881-906) INSERTs into `canonical_faqs` but never denormalizes back, so every cluster shows `label=NULL` in analytics and the diagnostics report.

**Files:**
- Modify: `app/workers/tasks.py:881-906` (`_persist_canonical_faq`)
- Test: Create `app/tests/unit/test_persist_canonical_faq.py`

- [ ] **Step 1: Write the failing test**

Create `app/tests/unit/test_persist_canonical_faq.py`:

```python
"""Unit tests: canonical FAQ persistence backfills the cluster row."""

from __future__ import annotations

from uuid import uuid4

from app.workers.tasks import _persist_canonical_faq


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.statements.append((str(clause), params or {}))


def test_faq_insert_also_backfills_cluster_label() -> None:
    session = _RecordingSession()
    cluster_id = uuid4()
    faq = {
        "cluster_id": cluster_id,
        "canonical_question": "प्रीमियम भुगतान की आवृत्ति कैसे बदलें?",
        "canonical_question_en": "How do I change my premium payment frequency?",
        "suggested_answer": None,
        "language": "hi",
        "confidence": 0.8,
        "version": 1,
    }

    _persist_canonical_faq(session, faq)

    assert len(session.statements) == 2
    insert_sql, _ = session.statements[0]
    update_sql, update_params = session.statements[1]
    assert "insert into canonical_faqs" in insert_sql.lower()
    assert "update semantic_clusters" in update_sql.lower()
    assert update_params["cluster_id"] == str(cluster_id)
    # Label prefers the English canonical form.
    assert update_params["label"] == "How do I change my premium payment frequency?"
    assert update_params["canonical_question"].startswith("प्रीमियम")


def test_label_falls_back_to_original_language() -> None:
    session = _RecordingSession()
    faq = {
        "cluster_id": uuid4(),
        "canonical_question": "How do I file a claim?",
        "canonical_question_en": None,
        "language": "en",
        "confidence": 0.9,
        "version": 1,
    }

    _persist_canonical_faq(session, faq)

    _, update_params = session.statements[1]
    assert update_params["label"] == "How do I file a claim?"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_persist_canonical_faq.py -v`
Expected: FAIL — only 1 statement recorded today.

- [ ] **Step 3: Implement the backfill**

In `app/workers/tasks.py`, append to `_persist_canonical_faq` (after the existing INSERT, inside the function):

```python
    # Denormalize onto the cluster row so analytics/diagnostics see a label
    # without joining canonical_faqs. English form preferred for the label.
    label = (d.get("canonical_question_en") or d.get("canonical_question") or "")[:120]
    session.execute(
        text(
            """
            UPDATE semantic_clusters
            SET canonical_question = :canonical_question,
                label = :label,
                last_updated = NOW()
            WHERE id = :cluster_id
            """
        ),
        {
            "cluster_id": str(d["cluster_id"]),
            "canonical_question": d.get("canonical_question", ""),
            "label": label,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m pytest app/tests/unit/test_persist_canonical_faq.py app/tests/unit/test_tasks_routing.py app/tests/unit/test_canonicalization.py -v`
Expected: PASS.

- [ ] **Step 5: Add unassigned-count observability to `cluster_call` (no test — log line only)**

In `app/workers/tasks.py`, change the `cluster_done` log line (line 455):

```python
        log.info(
            "cluster_done",
            n_members=len(members),
            n_unassigned=len(embeddings) - len(members),
        )
```

- [ ] **Step 6: Commit**

```bash
git add app/workers/tasks.py app/tests/unit/test_persist_canonical_faq.py
git commit -m "fix(canonicalize): backfill cluster label/canonical_question; log unassigned count"
```

---

### Task 6: Re-extraction script (operational, gated by --apply)

To re-measure on clean data we must purge the polluted extraction artifacts for already-processed calls and re-enqueue `v2t.extract`. Deleting `extracted_questions` cascades to `embeddings` and `cluster_members` (FK ON DELETE CASCADE — verified in the live schema), so clusters left with zero members must be pruned too.

**Files:**
- Create: `app/scripts/reextract.py`
- Test: dry-run mode IS the test (operational script; no unit test — it is one transaction of straightforward SQL plus Celery `send_task`).

- [ ] **Step 1: Write the script**

Create `app/scripts/reextract.py`:

```python
"""Re-enqueue extraction for already-processed calls.

Default is a DRY RUN that only prints what would happen. With ``--apply``:
  1. deletes extracted_questions for the selected calls (cascades to
     embeddings + cluster_members),
  2. prunes semantic_clusters left with zero members (and their
     canonical_faqs / memory_edges via FK cascade),
  3. resets call status to diarization_done,
  4. enqueues v2t.extract per call.

Usage::

    python -m app.scripts.reextract                  # dry run
    python -m app.scripts.reextract --apply
    python -m app.scripts.reextract --apply --statuses clustered,extraction_done
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.workers.celery_app import celery_app
from app.workers.db import sync_session

logger = get_logger(__name__)

DEFAULT_STATUSES = "clustered,extraction_done,embedding_done"


def run(statuses: list[str], apply: bool) -> int:
    with sync_session() as session:
        calls = session.execute(
            text("SELECT id, status FROM calls WHERE status = ANY(:s) ORDER BY created_at"),
            {"s": statuses},
        ).mappings().all()
        n_questions = session.execute(
            text(
                "SELECT COUNT(*) FROM extracted_questions"
                " WHERE call_id = ANY(:ids)"
            ),
            {"ids": [str(c["id"]) for c in calls]},
        ).scalar_one() if calls else 0

        print(f"Selected {len(calls)} calls (statuses={statuses}); "
              f"{n_questions} extracted_questions would be purged.")
        if not calls:
            return 0
        if not apply:
            print("DRY RUN — re-run with --apply to execute.")
            return 0

        ids = [str(c["id"]) for c in calls]
        session.execute(
            text("DELETE FROM extracted_questions WHERE call_id = ANY(:ids)"),
            {"ids": ids},
        )
        pruned = session.execute(
            text(
                """
                DELETE FROM semantic_clusters
                WHERE id NOT IN (SELECT DISTINCT cluster_id FROM cluster_members)
                RETURNING id
                """
            )
        ).fetchall()
        session.execute(
            text(
                "UPDATE calls SET status = 'diarization_done', updated_at = NOW()"
                " WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        print(f"Purged questions for {len(ids)} calls; pruned {len(pruned)} empty clusters.")

    for cid in ids:
        celery_app.send_task("v2t.extract", args=[cid])
    print(f"Enqueued v2t.extract for {len(ids)} calls.")
    return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="app.scripts.reextract")
    parser.add_argument("--apply", action="store_true", help="Execute (default: dry run).")
    parser.add_argument("--statuses", default=DEFAULT_STATUSES)
    args = parser.parse_args()
    return run([s.strip() for s in args.statuses.split(",") if s.strip()], args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
```

Assumptions verified against the code on 2026-06-10 (no need to re-check):
- `sync_session` commits on clean context exit (`app/workers/db.py:63-79`).
- The Celery app is `app.workers.celery_app.celery_app` (`app/workers/celery_app.py:30`).
- `CallStatus.DIARIZATION_DONE = "diarization_done"` exists (`app/models/enums.py:59`).

- [ ] **Step 2: Dry-run it against the live DB**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m app.scripts.reextract`
Expected output (numbers from the live DB): `Selected 26+ calls ...; 16 extracted_questions would be purged.` then `DRY RUN — re-run with --apply to execute.`

- [ ] **Step 3: Commit the script (do NOT --apply yet — workers still run old images)**

```bash
git add app/scripts/reextract.py
git commit -m "feat(scripts): gated re-extraction script (purge + re-enqueue)"
```

---

### Task 7: Rebuild, re-run the pipeline, re-run diagnostics, record the comparison

**Files:**
- Create: `docs/superpowers/diagnostics/2026-06-XX-phase-a-rerun-comparison.md` (use the actual date)
- Modify: `docs/superpowers/CONTINUE-HERE.md`

- [ ] **Step 1: Rebuild images so workers pick up Tasks 1-5**

Run: `docker compose build api worker-cpu beat && docker compose up -d api worker-cpu beat`
Expected: rebuild succeeds; `docker ps` shows the three containers freshly started and healthy. (The STT worker image doesn't need rebuilding — extraction runs on worker-cpu; confirm with `grep -A5 "worker-cpu" docker-compose.yml` that it runs the celery queue serving `v2t.extract`.)

- [ ] **Step 2: Execute the re-extraction**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" --entrypoint python api -m app.scripts.reextract --apply`
Expected: `Purged questions for N calls; ... Enqueued v2t.extract for N calls.`
⚠️ This deletes rows from the live DB. The purged data is the 16 known-bad questions — already snapshotted in the Phase A findings JSON. Ollama must be running on the host (`curl -s http://localhost:11434/api/tags | head -c 200` to verify) before enqueueing.

- [ ] **Step 3: Monitor until the pipeline settles**

Run (repeat until stable, extraction of ~26 calls through a local LLM takes a while):
```bash
docker exec v2t-postgres psql -U compliance_user -d compliance_db -c \
  "SELECT status, COUNT(*) FROM calls GROUP BY status;
   SELECT COUNT(*) AS questions, COUNT(*) FILTER (WHERE intent='other') AS intent_other,
          COUNT(*) FILTER (WHERE utterance_id IS NULL) AS ungrounded
   FROM extracted_questions;"
```
Expected end state: calls back at `clustered`; `ungrounded` = 0 (guard active); `intent_other` well below 80% of questions. Check worker logs for the new events: `docker logs v2t-worker-cpu 2>&1 | grep -E "fields_defaulted|ungrounded_question|language_bucket" | tail -20`.

- [ ] **Step 4: Re-run the Phase A diagnostics**

Run: `docker compose run --rm --no-deps -v "$(pwd)/app:/app/app" -v "$(pwd)/docs:/app/docs" --entrypoint python api -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300 --out-dir docs/superpowers/diagnostics/rerun`
Expected: new findings under `docs/superpowers/diagnostics/rerun/`.

- [ ] **Step 5: Write the comparison + take the gated decisions**

Create `docs/superpowers/diagnostics/2026-06-XX-phase-a-rerun-comparison.md` comparing before (`2026-06-08-phase-a-findings.json`) vs after (`rerun/...json`): n questions, intent distribution (esp. claim_*), QuestionType distribution, n_clusters/n_coarse, cluster-size stats. Then answer the three gated decisions from `2026-06-10-task4-interpretation.md` §"The three gated decisions" with the new numbers — this re-opens the ORIGINAL Phase B scope (HDBSCAN granularity tuning, claim sub-taxonomy, Top-Issues endpoint) as the next plan.

- [ ] **Step 6: Update CONTINUE-HERE.md and commit**

Update the TL;DR in `docs/superpowers/CONTINUE-HERE.md`: Phase B reliability DONE, point at the comparison doc, state the next decision (granularity tuning / sub-taxonomy / Phase C) per the new numbers.

```bash
git add docs/superpowers/diagnostics/ docs/superpowers/CONTINUE-HERE.md
git commit -m "docs(diagnostics): post-fix pipeline re-run findings and Phase B decisions"
```

---

## Self-review notes

- **Spec coverage:** interpretation doc §"Re-scoped Phase B" items B1 (Tasks 1-2), B2 (Task 3), B3 (Tasks 4-5), B4 (Tasks 6-7). ✓
- **Known risks called out inline:** `caplog` vs structlog capture (Task 1 Step 1), `UtteranceSchema` field names (Task 2 Step 1), worker-cpu queue assumption (Task 7 Step 1). Task 6's environment assumptions were verified against the code while writing this plan. Executors must verify the remaining ones at the marked steps, not assume.
- **Type consistency:** `_coerce_questions(payload, call_id, chunk)` signature unchanged; `extraction_drop_ungrounded` named identically in config and extractor; `_persist_canonical_faq(session, faq)` signature unchanged.
