# CONTINUE HERE — Issue & Discrepancy Analytics

**Last worked:** 2026-06-10 · **Branch:** `main`

This file is the resume point. Read it first, then the Task 4 interpretation, then execute Phase B.

---

## TL;DR — what to do next

1. **Phase B (extraction reliability) is DONE** (2026-06-10, branch `feat/phase-b-extraction-reliability`, 9 commits, 139 unit tests green, fully reviewed). Read `docs/superpowers/diagnostics/2026-06-10-phase-b-rerun-comparison.md` first — it has the before/after numbers and the revisited gated decisions.
2. **Result:** the DB now holds only trustworthy questions — 4 questions, 100% grounded, 0 defaulted intents (was 16 questions, 81% hallucinated garbage). The new guards caught and dropped 12 hallucination attempts during the re-run. Clusters=0 is expected (4 questions < hdbscan_min_cluster_size=8).
3. **New binding constraint: data volume / extraction recall.** 23/27 calls yield zero questions (8 agent-only; ~17 Urdu where gemma4 recall looks weak). Next steps (Phase B.2 candidates, see comparison doc): ingest more query-rich calls; A/B a stronger multilingual model vs `gemma4:latest` for extraction (swap `llm_model` in config); once corpus has ≥ a few dozen questions, lower `hdbscan_min_cluster_size` and re-run `phase_a_diagnostics` for the original granularity decision. Phase C (`discrepancy_type`) stays scoped, gated on volume.
4. Earlier context: Task 4 findings `diagnostics/2026-06-08-phase-a-findings.{md,json}`, interpretation `diagnostics/2026-06-10-task4-interpretation.md`, executed plan `plans/2026-06-10-phase-b-extraction-reliability.md`.

### Run diagnostics on this machine (macOS — no local 3.11 venv)
```bash
docker compose run --rm --no-deps \
  -v "$(pwd)/app:/app/app" -v "$(pwd)/docs:/app/docs" \
  --entrypoint python api -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300
```
(The running `v2t-api` image predates the diagnostics code; the mount overlays current code. Don't `docker exec` into `v2t-api` for long scripts — its restart policy killed one mid-run.)

---

## Where we are

**Goal (re-scoped):** Aggregate **issue/discrepancy mining** — surface the topics customers query about most and the *types of insurance discrepancies* they face (claim/affidavit queries are a priority), at **finer granularity** than today. The reported pain is that clustering is **"too clustered and coarse."**

**Dropped (do not rebuild):** entity-event knowledge graph, per-customer journey, Neo4j, acoustic/emotion vectors, speech-to-speech RAG. Rationale + verification verdicts are in the spec's §7 Decision Record. ("Affidavit" was confirmed to mean **literal claim queries**, not a synonym for FAQs.)

**Key insight:** ~70% of the ask already ships (`intent_distribution` + `top_clusters` by frequency in `app/api/routes/analytics.py`; `QuestionType.complaint`/`grievance`; claim intents in `app/models/enums.py`). The real gaps are (a) topic-intent can't separate a *question* from a *discrepancy*, (b) no claim/affidavit sub-taxonomy, (c) clustering too coarse, (d) no issue-framed analytics view.

## Source documents
- **Spec:** `docs/superpowers/specs/2026-06-08-issue-discrepancy-analytics-design.md`
- **Plan (Phase A):** `docs/superpowers/plans/2026-06-08-phase-a-clustering-diagnostics.md`

## Done (Tasks 1–3, merged, reviewed, tested on Py 3.11)
- `app/diagnostics/cluster_metrics.py` — pure metrics: `normalized_entropy`, `intent_purity`, `size_stats`, `mean_cosine_distance_to_centroid`.
- `app/diagnostics/report.py` — `ClusterObservation`, `flag_coarse` (large **and** intent-impure = distinct issues merged), `assemble_findings`, `render_markdown`.
- `app/scripts/phase_a_diagnostics.py` — read-only DB-pull script → findings artifact. Tunables: `--top-n`, `--max-members`, `--no-dispersion`, `--size-threshold`, `--purity-threshold`.
- `app/scripts/ingest_dataset_sample.py` — dataset sampler/uploader (2026-06-10, see above).
- Tests: `app/tests/unit/test_cluster_metrics.py`, `app/tests/unit/test_diagnostics_report.py` (21 passing).

## Not done
- ~~**Task 4**~~ — DONE 2026-06-10; see `diagnostics/2026-06-10-task4-interpretation.md`.
- **Phase B (re-scoped)** — extraction reliability first; see plan `plans/2026-06-10-phase-b-extraction-reliability.md`.

## Runtime gotchas
- System `python` on the corp laptop is **3.10**; the project needs **3.11+** (`StrEnum`). Anything importing `app.db.*` must run via `.venv\Scripts\python.exe`. The pure `app/diagnostics/*` modules happen to run on 3.10, which can mask the problem.
- Host-run scripts need `DATABASE_URL` overridden to `localhost:5432` (the `.env` value uses the compose-network hostname `postgres`).
- Never run two postgres containers on the `postgres_data` volume at once (the old `v2t-postgres-diag` pattern conflicts with the compose `postgres` service).

---

## Decisions gated on Task 4 (then plan Phase B/C)

Read the findings and decide:
1. **Is clustering too coarse?** → `n_coarse` + the coarse-cluster table (distinct intents/glosses sharing one cluster). If yes, **Phase B** tunes HDBSCAN (`hdbscan_min_cluster_size` / `hdbscan_min_samples` / `hdbscan_metric` in `app/core/config.py`; current values: 8 / 4 / euclidean, also set in `.env`); record current values + a target to try, then re-run the diagnostics to compare before/after.
2. **Discrepancy separability** → QuestionType distribution: meaningful `complaint` volume, or everything `question`? Decides how much **Phase C**'s `discrepancy_type` field must carry.
3. **Claim/affidavit visibility** → are `claim_process` / `claim_rejection` / `document_request` distinguishable or lumped? Decides the **Phase B** sub-taxonomy.

**Phase B (expected):** claim/affidavit sub-taxonomy + clustering granularity tuning + a "Top Issues & Discrepancies" analytics endpoint/view.
**Phase C (expected):** new problem-based `discrepancy_type` field (separate from topic `intent`) covering both claim/document issues *and* expectation-vs-policy mismatches. Touches `app/models/enums.py`, `app/prompts/extraction.py` + `extraction_schema.py`, `app/db/models.py` + a migration, analytics.

## How to resume the workflow
Plans live under `docs/superpowers/`. Once Task 4's findings are committed, use the **superpowers:writing-plans** skill to turn them into a Phase B/C plan, then **superpowers:subagent-driven-development** to implement (fresh subagent per task + spec & code-quality review, the same loop used for Phase A).
