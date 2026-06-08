# Issue & Discrepancy Analytics — Design

**Date:** 2026-06-08
**Status:** Draft (approved shape, pending spec review)
**Author:** abhinav.chaturvedi@bajajlife.com (with Claude Code)

---

## 1. Goal & Non-Goals

### Goal

Across all processed calls, surface:

1. **The topics customers query about most.**
2. **The types of insurance discrepancies / issues customers face**, with
   **claim / affidavit queries** as a priority category.

…at a **finer granularity than the current pipeline produces.** The reported
pain is that the existing clustering is **"too clustered and coarse"** —
distinct issues get merged into a single fat cluster, so the analytics can't
separate them.

This is **aggregate issue mining**: the unit of interest is the *topic / issue
and its frequency*, not any per-customer relationship.

### Non-Goals (explicitly cut)

These were considered and **deliberately dropped**. Recorded here so they are not
re-proposed:

| Cut | Why |
|---|---|
| Entity-event knowledge graph (Customer→Event→Product) | The real ask is *aggregate* ("what are people calling about"), not per-customer multi-hop. The graph would solve a problem we don't have. |
| Per-customer journey / Customer 360 | Same — out of scope for aggregate issue mining. |
| Neo4j / dedicated graph DB | No graph workload to justify the operational burden. If shallow traversal is ever needed, model triples in the existing Postgres (or Apache AGE) first. |
| Acoustic / emotion embeddings as a retrieval modality | Verification (below) showed this is a research bet, not a deployable capability; emotion is a trained SER head, not raw-vector similarity. |
| Speech-to-speech / transcription-free RAG (VoxRAG / WavRAG) | Real papers, but small-scale / explicitly disclaim the paralinguistic-retrieval capability that was attributed to them. |

See **§7 (Decision Record)** for the evidence behind these cuts.

---

## 2. Baseline — What Already Exists

A meaningful share (~70%) of the stated ask is **already shipping** in the `v2t`
pipeline. The current flow:

```
calls → utterances → extracted_questions → embeddings
      → semantic_clusters (with `frequency`) → canonical_faqs
```

| Requirement | Already implemented | Location |
|---|---|---|
| "Topics customers query most" | `intent_distribution` + `top_clusters` ranked by `frequency` | `app/api/routes/analytics.py:56-89` |
| "Claim queries" | `Intent.claim_process`, `Intent.claim_rejection`, `Intent.document_request` | `app/models/enums.py:39-43` |
| "Problems / complaints" | `QuestionType.complaint`, `Intent.grievance`, `Intent.agent_complaint`; extraction prompt already pulls *"query, complaint, or doubt"* | `app/models/enums.py:67-71`, `app/prompts/extraction.py:18` |

**The genuine gap** (what the baseline cannot do):

1. **Topic ≠ problem.** `Intent` is *topic*-based (`premium_payment`). It cannot
   separate *"how do I pay my premium?"* (a question) from *"my premium was
   charged wrong"* (a discrepancy). Both collapse into `premium_payment`.
2. **No claim/affidavit granularity.** All claim-document queries fall into
   generic `claim_process` / `document_request`; affidavit / claim-paperwork
   queries have no dedicated surface.
3. **Clustering is too coarse.** Distinct issues get merged, so even the topics
   that *are* surfaced are lumped together.
4. **No issue-framed analytics view.** Analytics today is generic KPIs, not a
   "top issues & discrepancies by category, with examples and trend" view.

This design is an **extension** of the existing pipeline, not a rewrite.

---

## 3. Phase A — Verify & Quantify the Coarseness

**Intent:** Measure where granularity actually fails before changing any schema.
Cheap, no migrations. Output drives the concrete shape of Phases B and C.

**Method:** Run the existing pipeline on a real batch (`dataset/` audio +
`transcripts/*.txt` + `data/sample_transcripts/*.json`) and measure:

- **Surfaced today:** `intent_distribution`, `top_clusters` by frequency.
- **Cluster granularity diagnostics:**
  - Cluster size distribution.
  - **Intra-cluster heterogeneity** — sample the largest clusters and inspect
    members: are *distinct* issues (e.g. "surrender value" vs "maturity payout"
    vs "nominee change", all seen in `call_003`) wrongly merged into one cluster?
  - HDBSCAN parameters currently in effect (`min_cluster_size`, etc.) and their
    effect on granularity.
- **Question vs discrepancy separability:** in the current output, can a
  *complaint / discrepancy* be told apart from a plain *question*? (Empirical —
  does the extraction reliably catch e.g. *"my policy says X but the agent
  promised Y"*?)

**Output / acceptance:** A short findings note that **quantitatively** pins down
where granularity fails (which clusters are over-merged; whether discrepancies
are distinguishable; whether claim/affidavit queries are buried). This note
defines the exact taxonomy and clustering changes for B and C — so the ontology
is designed against real extractions, not in a vacuum.

---

## 4. Phase B — Targeted Extension (informed by A)

Final shape is set by Phase A's findings. Expected components:

1. **Claim / affidavit sub-taxonomy** so claim-document queries surface on their
   own rather than disappearing into generic `claim_process` /
   `document_request`. (Mechanism — new sub-intent field vs. extended `Intent`
   enum vs. secondary tag — chosen after A.)
2. **Clustering granularity tuning** — adjust HDBSCAN parameters
   (`min_cluster_size` and related) so the coarse-cluster problem measurably
   improves, **verified against Phase A's diagnostics** (re-run, compare
   intra-cluster heterogeneity before/after).
3. **"Top Issues & Discrepancies" analytics view/endpoint** — category →
   frequency → trend over time → drill-down to example calls. Extends, not
   replaces, the existing `/analytics` route.

---

## 5. Phase C — Discrepancy Dimension

Add a **problem-based** field, `discrepancy_type`, *separate from* the
topic-based `intent`. This is the structural fix for "topic ≠ problem."

Covers **both** senses of "discrepancy":

- **Claim / document issues** — affidavits, missing paperwork, claim rejections.
- **Expectation-vs-policy mismatches** — premium / benefit / coverage gaps
  (*"my policy says X but I was promised Y"*).

Touch points:

- `app/models/enums.py` — new `DiscrepancyType` enum (starter values informed by
  A; e.g. `premium_mismatch`, `promise_gap`, `coverage_confusion`, `claim_doc`,
  `none`).
- `app/prompts/extraction.py` + `app/prompts/extraction_schema.py` — add the
  field to the extraction contract.
- `app/db/models.py` + a migration — persist `discrepancy_type` on
  `extracted_questions`.
- Analytics — discrepancy-type breakdown alongside intent distribution.

---

## 6. Architecture Fit

- **Transcript + audio remain the immutable system of record** (regulatory
  anchor for a life insurer). All analytics are a **derived, rebuildable index**.
- No new infrastructure: everything lands in the existing Postgres + pgvector and
  the existing FastAPI / Celery / clustering stack.
- Changes follow existing patterns: closed-set `StrEnum`s as the single source of
  truth (`app/models/enums.py`), strict JSON-schema extraction
  (`extraction_schema.py`), Pydantic + SQLAlchemy 2.0 typed models.

---

## 7. Decision Record — Verification of the Original Proposal

The original "Canonical Knowledge Object + Hybrid GraphRAG" proposal cited a
number of named systems. A parallel verification pass (web research, one agent
per cluster of claims) found **most names are real papers but the attributed
capabilities are overstated or misdescribed** — the more dangerous failure mode,
because the real arXiv links make the claims look settled.

| Reference | Real? | Reality vs. claim | Verdict |
|---|---|---|---|
| VoxRAG | Yes (arXiv 2505.17326, ACL'25 *workshop*) | 50-query prototype, Recall@10≈0.34; zero mention of emotion/affect | Not evidence for emotion retrieval |
| WavRAG | Yes (arXiv 2502.14727, ACL'25 main) | Solid audio-RAG; its **own Limitations** call prosody/emotion "an open question" | Trust paper; discard emotion claim |
| ChipChat | Yes (arXiv 2509.00078, Apple/MLX) | Predicts per-turn motivation/emotion but **never validates it**; "proves…at every turn" is embellishment | Real, unvalidated |
| ChatRouter / HPLE | Yes (HP defensive publication) | Real routing arch, but **non-peer-reviewed** disclosure — design intent, not results | Real; treat as intent |
| NeMo Duplex Norm | Yes | **Misdescribed** — converts spoken↔written form ("$20"↔"twenty dollars"); does **not** canonicalize entity IDs | Wrong purpose |
| "Speaker-IPL diarization" | Name real (arXiv 2409.10791) | **Misattributed** — it's unsupervised speaker-*embedding* learning, **not** diarization; "1.5s sliding window" invented | Use pyannote / NeMo / Sortformer |
| T-VEC | Yes (arXiv 2504.16460) | Real FAISS embedding model, but **telecom-domain**-specialized | Existing e5 is the right fit |
| Hybrid GraphRAG | Yes (MS GraphRAG, LightRAG) | Real & useful for multi-hop — but extraction error compounds; rebuild/staleness cost | Legitimate, but not needed for *aggregate* mining |
| Acoustic emotion retrieval | — | Emotion is a *trained SER head*, not raw-vector similarity (which is dominated by speaker/channel) | Store SER labels, not vectors — and not needed here |

**Implication:** the grand graph/acoustic architecture was solving problems this
project does not have. The valuable, low-regret core is improving extraction and
clustering granularity on the existing pipeline — which is what this design does.

---

## 8. Open Questions / Risks

- **Phase A is a gate, not a formality.** B and C's exact taxonomy depend on its
  findings. If A shows the current output is already adequate, B/C shrink.
- **Extraction reliability for mismatches is empirical.** Whether the LLM
  reliably catches *"policy says X, agent promised Y"* must be confirmed on real
  data (Phase A), not assumed from the prompt.
- **Clustering tuning has trade-offs.** Smaller `min_cluster_size` reduces
  over-merging but increases noise/singletons; tune against A's diagnostics, not
  by feel.
