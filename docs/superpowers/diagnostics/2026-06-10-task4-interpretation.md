# Phase A Task 4 — Interpretation & Decision Record

**Date:** 2026-06-10 · **Inputs:** `2026-06-08-phase-a-findings.{md,json}` (real DB run) + three parallel root-cause investigations (extraction coverage, intent quality, cluster integrity).

## How the run was done

`app.scripts.phase_a_diagnostics --top-n 30 --max-members 300` executed inside a one-off
container from the `v2t/api:local` image (Python 3.11) with the local repo mounted over
`/app/app`, against the live `compliance_db`. Read-only. (No local 3.11 venv exists on this
machine; the running `v2t-api` image predates the diagnostics code.)

## Raw numbers

| Metric | Value |
|---|---|
| calls / utterances | 33 / 1,097 |
| calls at status `clustered` | 26 |
| calls with ≥1 extracted question | **3** (13 + 2 + 1) |
| extracted_questions / embeddings | 16 / 16 |
| semantic_clusters / cluster_members | 2 / 11 |
| coarse clusters flagged | 0 |
| QuestionType | question 14, doubt 2, complaint/grievance **0** |
| Intent | other **13**, other_insurance 2, policy_details 1; claim_* **0** |

## Root causes found (parallel investigations)

1. **Intent/confidence/gloss defaulting (95% confidence).** Gemma (`gemma4:latest` via
   Ollama) omits `intent`, `confidence`, `english_gloss` from its JSON for some batches.
   `ExtractedQuestion` Pydantic defaults (`intent=other`, `confidence=0.0`, `gloss=None`,
   `app/models/schemas.py:117-123`) silently absorb the omission; the coercion fallback in
   `app/services/extraction/llm_extractor.py:189-202` repairs `raw_text`/`language` aliases
   but not these fields. All 13 bad rows: one call, one timestamp, `utterance_id=NULL`.
2. **The 13 bad rows are ungrounded (hallucinated).** None of their `raw_text` values occur
   in the call's 982 utterances; `raw_text == normalized_text` exactly. The model generated
   FAQ-like questions instead of extracting.
3. **Extraction coverage gap.** Of 24 zero-question clustered calls: 8 have no customer
   utterances (legitimately empty); 16 have customer utterances but zero questions — 17 of
   the 24 are in the `other` language bucket (Urdu/Arabic script), which
   `detect_dominant_language()` (`app/prompts/extraction_lang.py`) doesn't handle, and the
   model plausibly returns valid-but-empty `{"questions": []}`. Errors were NOT swallowed —
   empty extraction is a "successful" result, and `cluster_call`
   (`app/workers/tasks.py:449`) advances status to `clustered` unconditionally.
4. **Cluster integrity bugs.** (a) Orphan cluster `0e71e142…` (frequency=4, 0 members):
   dissolved clusters get `is_stable=FALSE` but never a frequency recount
   (`app/services/factories.py:348-355`). (b) `label`/`canonical_question` NULL on both
   clusters: `_persist_canonical_faq` (`app/workers/tasks.py:881-906`) only INSERTs into
   `canonical_faqs`, never backfills `semantic_clusters`. (c) 5/16 questions unclustered:
   HDBSCAN noise — expected with `min_cluster_size=8`, `min_samples=4`, `metric=euclidean`
   (`app/core/config.py:117-119`) on a 16-point corpus.

## The three gated decisions

### 1. Is clustering too coarse? → **Cannot be judged yet; tuning HDBSCAN now is premature.**
The coarse-flag fired on 0 clusters, but only because the signal is blinded: the one real
cluster (11 members) reports `intent_purity=1.0` solely because all members are `other` —
exactly spec gap (a). Its glosses mix ≥3 distinct topics (premium-frequency change, policy
detail updates, policy status), and its members are the hallucinated rows anyway. With 16
questions from 3 calls, HDBSCAN parameters are not the binding constraint; extraction is.
**Decision: defer granularity tuning until extraction is fixed and re-run on all 26 calls.
Current values to re-test against: min_cluster_size=8, min_samples=4, euclidean.**

### 2. Discrepancy separability → **No signal in current data; Phase C field still justified.**
Zero `complaint`/`grievance` rows — but that measures extraction failure, not customer
behaviour. Since intent/question_type currently default on omission, a future
`discrepancy_type` would default the same way. **Decision: Phase C's discrepancy_type
stays scoped but is blocked behind extraction reliability (strict field validation).**

### 3. Claim/affidavit visibility → **Indistinguishable — but because intents never get assigned at all.**
`claim_process`/`claim_rejection`/`document_request` exist in the enum and in the prompt
taxonomy (`app/prompts/extraction.py:30-33`); 0 rows carry them because Gemma omits the
field and the default is `other`. **Decision: no sub-taxonomy design until intents are
reliably assigned; re-evaluate after the extraction fix + re-run.**

## Re-scoped Phase B (extraction reliability first)

1. **B1 — Strict extraction validation & grounding.** Reject/retry (or at minimum
   WARN-log and flag) LLM items missing intent/confidence/gloss; flag questions whose
   `raw_text` matches no utterance (`utterance_id IS NULL`) as ungrounded; surface
   per-call `n_questions` + language bucket in logs.
2. **B2 — Language coverage.** Handle Urdu/Arabic-script (`other` bucket) in
   `extraction_lang.py` (detection + prompt overlay); verify Gemma on Urdu samples, else
   consider a multilingual model for extraction.
3. **B3 — Cluster integrity.** Recount frequency for dissolved clusters; backfill
   `semantic_clusters.label`/`canonical_question` from `canonical_faqs`; log noise counts
   per rebatch.
4. **B4 — Re-run & re-measure.** Re-extract the 26 calls, re-run
   `phase_a_diagnostics`, then take the original Phase B granularity/sub-taxonomy
   decisions on trustworthy numbers.

Original Phase B items (HDBSCAN tuning, claim sub-taxonomy, "Top Issues" endpoint) move
behind B4. Phase C (discrepancy_type) unchanged in scope, still gated.
