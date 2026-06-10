# Phase B — Post-Fix Re-Run: Before/After & Gated Decisions

**Date:** 2026-06-10 · **Branch:** `feat/phase-b-extraction-reliability` · **Before:** `2026-06-08-phase-a-findings.{md,json}` · **After:** live `compliance_db` after `reextract --apply` (27 calls re-extracted through the fixed pipeline; rebuilt images).

## Before / after

| Metric | Before (Task 4) | After (Phase B re-run) |
|---|---|---|
| extracted_questions | 16 | **4** |
| grounded (utterance_id set) | 3 (19%) | **4 (100%)** |
| hallucinated rows persisted | 13 | **0** (12 attempts caught & dropped) |
| intent = `other` (defaulted) | 13 (81%) | **0** |
| confidence = 0.0 | 13 | **0** (all 0.9–0.95) |
| english_gloss present | 3 | 4 |
| QuestionType | question 14, doubt 2 | doubt 3, question 1 |
| semantic_clusters / members | 2 / 11 (one orphan, labels NULL) | 0 / 0 (see below) |

Guard events during the run (worker logs): `ungrounded_question` ×12, `fields_defaulted` ×12 —
the same hallucinated items Gemma produced before are still being produced, but they are now
logged, counted (`extraction_processed{status="ungrounded"|"degraded"}`), and **never persisted**.

The question count dropping 16→4 is the fix working, not a regression: the DB now contains
only verbatim-grounded customer questions with model-assigned intents. The 11 rows previously
flagged `human_override=TRUE` were confirmed hallucinations (flagged during feedback-route
testing, not genuine review) and were purged with a warning.

**Clusters = 0 is expected**, not a bug: 4 questions < `hdbscan_min_cluster_size=8`, so HDBSCAN
classifies everything as noise and `phase_a_diagnostics` exits with "No clusters found"
(hence no `rerun/` artifact). The two stale clusters (including the frequency=4/0-member
orphan) were pruned by the reextract script.

## The three gated decisions, revisited

1. **Clustering granularity (HDBSCAN tuning):** still not assessable — but the blocker has
   moved from *data quality* to *data volume*. 4 trustworthy questions cannot exercise any
   clustering parameters. Tuning stays deferred until the corpus grows (more calls ingested,
   and/or extraction recall improves on the Urdu-majority corpus).
2. **Discrepancy separability:** zero complaints in 4 questions says nothing yet.
   `discrepancy_type` (Phase C) stays scoped, still gated on volume.
3. **Claim/affidavit visibility:** intents are now genuinely assigned (`policy_details`,
   `other_insurance` — matching what these verification-style calls actually contain). The
   claim sub-taxonomy decision needs claim-related call volume to evaluate.

## New binding constraint: extraction recall on the real corpus

23 of 27 calls yielded zero questions. Two known contributors, in likely order of impact:
- **8 calls have no customer utterances** (agent-only verification calls — correctly empty).
- **~17 calls are Urdu** (Arabic script). The new `ur` prompt overlay ships in this run, but
  yield stayed near zero — either these verification calls genuinely contain few customer
  questions, or `gemma4` extraction recall on Urdu content is poor (its 12 hallucination
  attempts on English-ish content suggest the model is weak at grounded extraction overall).

**Recommended next steps (Phase B.2 candidates):**
1. Ingest more calls — especially ones known to contain customer queries/complaints — and
   watch `extraction_processed` statuses; the pipeline is now trustworthy enough to measure.
2. Evaluate a stronger/multilingual extraction model against `gemma4:latest` on a handful of
   Urdu calls (the config already supports swapping `llm_model`); compare grounded-question
   yield using the new metrics.
3. Once questions ≥ a few dozen, lower `hdbscan_min_cluster_size` from 8 (it guarantees
   noise-only output below 8 questions per topic) and re-run `phase_a_diagnostics` for the
   original granularity decision.

## Phase B reliability fixes shipped (all reviewed, 139 unit tests green)

- `extractor.fields_defaulted` logging + `degraded` metric (kept-rows-only semantics).
- Ungrounded-question drop behind `extraction_drop_ungrounded=True` (config escape hatch).
- Invalid `language` enum values repaired instead of dropping the question.
- `ur` language bucket: Arabic-script detection + Urdu prompt overlay (+ enum steering).
- Dissolved clusters recount `frequency` (orphan bug fixed at the source).
- Canonical FAQ backfills `semantic_clusters.label`/`canonical_question` (version-guarded,
  empty-label safe).
- `cluster_done` logs `n_unassigned`; reextract script (dry-run default, enqueue-failure
  recovery guidance, human-override purge warning).
