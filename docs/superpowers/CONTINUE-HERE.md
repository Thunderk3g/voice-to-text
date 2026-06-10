# CONTINUE HERE — Issue & Discrepancy Analytics

**Last worked:** 2026-06-10 · **Branch:** `main`

This file is the resume point. Read it first, then the spec, then run the runbook below.

---

## TL;DR — what to do next

Phase A **Task 4 is still pending** — it needs the pipeline to run on real data, which
requires unproxied internet (Sarvam STT + Groq LLM calls). Run the runbook below on the
laptop **without the corporate proxy**, then plan Phase B/C from the findings.

## What the 2026-06-10 session found (state of the world)

- **There is no populated DB.** The `voice-to-text_postgres_data` volume has the full
  migrated schema but **zero rows** in every table (`calls`, `extracted_questions`,
  `semantic_clusters`, …). A leftover `v2t-postgres-diag` container (same volume,
  host port 15432) confirmed this; it has been stopped and removed.
- **The real data is local audio:** `dataset/` holds **11,460 mp3s (~1.1 GB)** of real
  calls. (17 of them are git-tracked; the rest are untracked. A stray `git status`
  deletion of the 17 tracked ones was restored with `git checkout -- dataset/`.)
- `transcripts/*.txt` (17 files) are loose STT outputs — **not ingestible** (the API
  accepts `.json` transcripts or audio only).
- `.env` has working `SARVAM_API_KEY` and `LLM_API_KEY` (Groq), so the full
  audio → STT → extraction → embedding → clustering pipeline is runnable.
- **New script:** `app/scripts/ingest_dataset_sample.py` — uploads a reproducible
  random sample of `dataset/*.mp3` to `/ingest/upload` (seeded RNG, idempotent via
  `dataset/.ingested.json`, batched with progress output). Written for Task 4; not
  yet exercised against a live API.

## Runbook — Task 4 on the unproxied laptop

```powershell
# 0. One-time: Python 3.11 venv (system python is 3.10 on the corp laptop — check yours)
uv venv --python 3.11; .venv\Scripts\pip install -r requirements.txt

# 1. Full stack up (postgres, redis, minio, api, worker-cpu, worker-embed, beat)
docker compose up -d --build

# 2. Seed the 6 bundled sample transcripts (fast, no STT)
.venv\Scripts\python.exe -m app.scripts.seed_data --api-url http://localhost:8080

# 3. Ingest a representative sample of real calls (500 ≈ enough for granularity
#    diagnostics; scale --n up if you have time/credit — state file makes it additive)
.venv\Scripts\python.exe -m app.scripts.ingest_dataset_sample --n 500

# 4. Wait for the pipeline to drain. Watch progress until (clustered + failed) ≈ total:
docker exec v2t-postgres psql -U compliance_user -d compliance_db -c "SELECT status, count(*) FROM calls GROUP BY status;"
#    (Flower is on http://localhost:5555 for queue depth.)

# 5. Force a full HDBSCAN pass (beat only runs it at 02:00 UTC):
docker exec v2t-worker-cpu celery -A app.workers.celery_app call v2t.batch_recluster

# 6. Run the diagnostics from the host (note the DATABASE_URL override —
#    .env points at the in-network hostname `postgres`, not localhost):
$env:DATABASE_URL = "postgresql+asyncpg://compliance_user:compliance_pass@localhost:5432/compliance_db"
.venv\Scripts\python.exe -m app.scripts.phase_a_diagnostics --top-n 30 --max-members 300

# 7. Commit the findings artifact:
git add docs/superpowers/diagnostics/2026-06-08-phase-a-findings.md docs/superpowers/diagnostics/2026-06-08-phase-a-findings.json
git commit -m "docs(diagnostics): phase-A findings on clustering/extraction granularity"
```

Notes:
- Container names: check with `docker ps` — worker container may be `v2t-worker-cpu`
  or similar per `docker-compose.yml` `container_name`.
- On the unproxied laptop the corporate-CA TLS bypass baked into the 5 Dockerfiles is
  unnecessary (it's marked for removal); it shouldn't break anything, but if TLS
  errors appear, that's the first place to look.
- `seed_data` / `ingest_dataset_sample` are idempotent (`.seeded.json` /
  `.ingested.json` state files, both untracked) — safe to re-run.

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
- **Task 4** — populate the DB (runbook above) + run the diagnostics and record findings. **This gates Phase B/C.**

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
