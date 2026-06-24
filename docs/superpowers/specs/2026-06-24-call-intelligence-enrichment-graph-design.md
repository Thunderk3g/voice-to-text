# Call Intelligence: Lead Extraction, Disposition, Sentiment & Knowledge Graph

**Date:** 2026-06-24
**Status:** Design — pending user review
**Owner:** abhinav.chaturvedi@bajajlife.com
**Repos touched:** `voice-to-text` (primary), `Campaign Intelligence` (one new join script)

---

## 1. Purpose & background

The `voice-to-text` (V2T) pipeline was built as an insurance call-intelligence system but has so far over-invested in **speaker diarization + FAQ clustering**. The actual business need is different:

> From ~747,000 old inbound call-center recordings (`D:\crux_calls\2026\MM\DD\<crux_id>.mp3`), extract **(a) lead information, (b) call disposition, (c) sentiment**, use them to **enrich the existing lead master** held by the separate *Campaign Intelligence* (CI) app, and expose the result as an **Obsidian-style knowledge graph** linking leads, calls, agents, campaigns, products, dispositions and sentiment.

The good news from codebase analysis: most of the machinery already exists. This design **extends the existing pipeline in place** rather than rebuilding, and **reuses** the LLM extraction scaffolding, pgvector/embedding stack, the edge upsert/dedup pattern, and the Cytoscape graph viewer.

### Key facts established during discovery

- **Corpus:** ~747k mp3, foldered `2026/MM/DD`, named by an **8-digit Crux recording id**. The only index is `D:\crux_calls\dataset\voice_to_text_index\duration_index.csv` (`original_path, filename, duration_sec, bucket, status, new_path`) — **no phone/lead/agent columns**. Only ~27 calls ingested so far.
- **Enrichment target:** CI `marts/leads_canonical.csv` = **977,360 leads**, key `LEAD_NO`, join key `MOBILE_NO`. Columns include `CUST_FIRST_NAME, CUST_FULL_NAME, CUST_AGE, CUST_DOB, CUST_GENDER, CUST_EMAIL_ID, OCCUPATION, EDUCATION, ANNUAL_INCOME, PIN_CODE, PROD_ID, PRODUCT_TYPE, PRIORITY, DISPOSITION, AGE_GROUP, INCOME_GROUP, MOBILE_VALID, ENRICHED`.
- **Existing `DISPOSITION` column** is an **outbound LMS-telecalling** vocabulary (`Ringing`, `Just Browsing`, `NW - Incorrect Request`, `NE - NE due to income`, `NI - Due to Price`, `Meeting Scheduled`, …) — a *different axis* from inbound-service call outcomes. We therefore do **not** overwrite it.
- **Live stack (`.env`):** STT = Sarvam `saaras:v3` (2-speaker); LLM = Groq `openai/gpt-oss-120b` (OpenAI-compatible `/v1`, strict JSON mode); embeddings = local `intfloat/multilingual-e5-large` (1024-d, CPU).
- **The join key is the phone number** — it appears nowhere in the corpus index, so it must come from a **Crux CDR export** (recording_id → caller number; user has confirmed this is obtainable) and/or be **extracted from the transcript**.

---

## 2. Goals & non-goals

### Goals
1. A **per-call analyzer** that, in **one Groq pass over the whole transcript**, returns `{lead, disposition, sentiment}`.
2. A **deterministic call→lead join** via a Crux CDR (recording_id → caller phone → normalized mobile), with transcript-extracted phone as fallback / cross-check.
3. **Additive enrichment** of CI `leads_canonical.csv` with `CALL_*` columns (CI remains the lead-master owner).
4. A **typed knowledge graph** in Postgres (lead / call / agent / campaign / product / disposition / sentiment nodes + typed edges) feeding **both** the existing Cytoscape viewer **and** an **Obsidian vault exporter**.
5. **Slice-first validation** before any corpus-scale STT spend.

### Non-goals (this iteration)
- Audio emotion / acoustic SER (sentiment is text/LLM-derived; SER was explicitly scoped out earlier).
- Re-architecting the FAQ clustering / memory-edge subsystem — it is made **optional** for this path, not deleted.
- Transcribing all 747k calls up front.
- Real-time / streaming analysis (batch only).
- Migrating the CI lead master into Postgres (CI keeps ownership).

---

## 3. Current state → what we reuse, extend, add, trim

| Concern | Today | Plan |
|---|---|---|
| STT | Sarvam `saaras:v3` / faster-whisper, `make_transcriber()/transcribe_file()` | **Reuse**; confirm in-language transcript for grounding (see §12) |
| LLM extraction | `llm_extractor.py` emits `questions[]` only | **Clone** scaffolding into `CallAnalyzer` (whole-transcript, both speakers) |
| Lead info | none | **Add** `lead{}` object in analyzer output |
| Disposition | none (Intent enum is topical) | **Add** `CallDisposition` enum + crosswalk |
| Sentiment | none | **Add** `SentimentLabel` + escalation flag |
| Join key | not retained at ingest | **Add** CDR loader + retain `crux_call_id`; port `normalize_mobile` |
| Enrichment | none | **Add** `call_summary` exporter + CI `merge_call_intel.py` |
| Graph | cluster-only `memory_edges` + Cytoscape | **Generalize** to typed `graph_edges` + node tables; **add** Obsidian exporter |
| Diarization / pyannote | heavy, on by default-ish | **Trim**: make optional extra; not on the lead path |
| FAQ clustering tail (`embed→cluster→canonicalize→memory_edges`) | runs in pipeline | **Trim**: no-op in lead pipeline mode |

---

## 4. Architecture overview

```
mp3 (crux_id)
   │  ingest  ── retain crux_call_id in calls.metadata.extra
   ▼
calls row ──► v2t.transcribe (Sarvam) ──► utterances
                                              │
                  CDR lookup (crux_call_id → caller phone, agent, queue, ts)
                                              │
                                              ▼
                          NEW  v2t.analyze  (one Groq gpt-oss-120b pass, whole transcript)
                                   │  → CallAnalysis { lead{}, disposition, sentiment{} }
                                   │     persisted to call_analysis (JSONB-first, then table)
                          ┌────────┴───────────────────────────────────┐
                          ▼                                             ▼
          enrichment exporter                              knowledge-graph builder
   per-mobile call_summary.csv                     upsert typed nodes + graph_edges
            │  (additive LEFT JOIN, imitates                     │
            │   merge_leads_activity.py)               ┌─────────┴──────────┐
            ▼                                           ▼                    ▼
   CI leads_canonical + CALL_* cols        /knowledge-graph API      Obsidian vault export
   (CI owns the master)                    → Cytoscape viewer        (md notes + [[wikilinks]])
```

The diarization → FAQ-clustering tail is bypassed when `pipeline_mode = lead`.

---

## 5. Components

Each component has **one responsibility**, a defined **interface**, and explicit **dependencies**.

### 5.1 Ingest — retain the Crux call id
- **Does:** at registration, write the numeric mp3 filename into `calls.metadata.extra.crux_call_id` (currently discarded). If a CDR context is already known, also stamp `caller_phone`, `agent_id`, `campaign`, `direction`.
- **Interface:** existing `/ingest` + `ingest_dataset_sample.py`, extended.
- **Depends on:** `CallMetadata.extra` JSONB (exists).

### 5.2 CDR loader & join resolver — `app/services/cdr/`
- **Does:** load a Crux CDR export (CSV) into a `cdr_records` table; expose `resolve(crux_call_id) -> CallContext{caller_phone, agent_id, queue, started_at, direction}`.
- **Interface:** `load_cdr(path)`, `resolve(crux_call_id)`.
- **Depends on:** `normalize_mobile` (§5.3), DB.
- **Note:** the CDR schema is unknown until the user supplies a sample; loader is written against a small declared column mapping (config), so a new export shape is a config change, not code.

### 5.3 Phone normalization — `app/utils/phone.py`
- **Does:** `normalize_mobile(value) -> str|None` — a **verbatim port** of CI `src/normalize.py` (strip non-digits, drop `.0`, drop leading `91`/`0`, require 10 digits starting 6-9).
- **Interface:** `normalize_mobile`, `clean_na`.
- **Depends on:** nothing. **Tested against CI's `tests/test_normalize.py` cases** to guarantee join parity.

### 5.4 CallAnalyzer — `app/services/extraction/call_analysis.py`
- **Does:** one `chat_json` pass over the **whole transcript (both speakers)** → `CallAnalysis{lead, disposition, sentiment}`. Long calls use map-reduce: per-chunk partials merged into one object (most inbound calls are short — one chunk).
- **Interface:** `CallAnalyzer(client).analyze(call_id, utterances, cdr_context) -> CallAnalysis`.
- **Reuses (clones from `llm_extractor.py`):** `GroqClient.chat_json`, `detect_dominant_language`/`system_prompt_for` language overlays, `_match_utterance_id` grounding (for phone/name verbatim), Pydantic coerce-and-repair, Prometheus `extraction_processed`-style counters.
- **Grounding guard:** extracted phone/name/email must trace to an utterance substring or be dropped/flagged (prevents PII hallucination).
- **New prompt module:** `app/prompts/call_analysis.py` + `CALL_ANALYSIS_SCHEMA` in `app/prompts/call_analysis_schema.py`.

### 5.5 Enums & taxonomy — `app/models/enums.py` + crosswalk
- `CallDisposition` (StrEnum), `SentimentLabel` (StrEnum). See §8.
- `app/data/disposition_crosswalk.yaml`: maps `CallDisposition` ↔ LMS `DISPOSITION` families.

### 5.6 Persistence — `call_analysis` table
- **Does:** 1:1 with `calls`: `lead jsonb, disposition, disposition_confidence, sentiment, escalation bool, sentiment_confidence, raw_response, model, analyzed_at`.
- **Strategy:** prototype in `calls.metadata` JSONB first (zero migration) to iterate fast; promote to a first-class table in P3 via additive Alembic migration.

### 5.7 Pipeline wiring — `v2t.analyze` task
- **Does:** new Celery task `v2t.analyze` (sibling of `v2t.extract`), inserted into the `_next(...)` handoff after `v2t.transcribe`.
- **Config:** `settings.pipeline_mode ∈ {lead, faq, both}`. `lead` ends after `analyze` (skips embed/cluster/canonicalize/memory_edges). `both` continues into the FAQ tail.
- **Depends on:** §5.4, §5.6. Uses existing `acks_late`/`prefetch=1`/`set_call_status` patterns.

### 5.8 Enrichment exporter — `app/services/enrichment/` + CI `src/merge_call_intel.py`
- **V2T side:** build a **per-mobile `call_summary`** (aggregate multiple calls per phone): `PHONE_NUMBER, CALL_DISPOSITION, CALL_SENTIMENT, CALL_ESCALATION, CALL_N_CALLS, CALL_LAST_DATE, CALL_LEAD_NAME, CALL_LEAD_EMAIL, CALL_LEAD_OCCUPATION, CALL_LEAD_INCOME_BAND, CALL_LEAD_PRODUCT_INTEREST, CALL_LEAD_PINCODE, CALL_SOURCE_CALL_IDS, CALL_CONFIDENCE`. Write `call_summary.csv`.
- **CI side (new script):** `merge_call_intel.py` imitates `merge_leads_activity.py` — `pd.merge(leads, call_summary, left_on="MOBILE_NO", right_on="PHONE_NUMBER", how="left")`, **append-only** (never mutate existing columns; CI `enhance_leads.py` convention).
- **Many-to-one** (`MOBILE_NO` → multiple `LEAD_NO`): documented tie-break (default: most-recent FY, then highest `PRIORITY`). **Unmatched calls** (no matching lead, or `MOBILE_VALID=false`) go to an `unmatched_calls.csv` bucket.

### 5.9 Typed knowledge graph — `app/services/knowledge_graph/`
- **Generalize** `memory_edges` → generic `graph_edges(id, src_id, src_type, dst_id, dst_type, relation, weight, reason, created_at, UNIQUE(src_id, dst_id, relation))`.
- Add node tables: `lead, agent, campaign, product` (`call`, `disposition`, `sentiment` map to existing/enum-backed rows).
- **Builder** upserts edges (reuses `MemoryEdgeRepo` `ON CONFLICT` pattern); entity resolution for agent/product names via existing embedding cosine-dedup.
- **API:** `GET /knowledge-graph` (typed `{nodes, edges}`, paged/aggregated server-side; the old flat `limit=500` dump won't scale).

### 5.10 Graph delivery
- **Cytoscape viewer:** generalize `frontend/components/CytoscapeGraph.tsx` with a node `type` discriminator + per-type styling (`node[type='lead']` selectors); new route consuming `/knowledge-graph` (mock API first).
- **Obsidian exporter:** `app/services/knowledge_graph/obsidian_export.py` + CLI `app/scripts/export_obsidian.py`. Emits a vault: one `.md` note per entity in type folders (`leads/`, `calls/`, `agents/`, `campaigns/`, `products/`), YAML frontmatter (`type, id, disposition, sentiment, metrics`), body with `[[wikilinks]]` to related notes + a Dataview-friendly table. **Scoped to a slice** (per-campaign / P0 leads) — Obsidian's graph view won't render millions of notes.

### 5.11 Bulk ingestion & slice selection
- Extend `ingest_dataset_sample.py` to drive from `duration_index.csv`, filter to a **slice** (see §10), with `.ingested.json` resumability. Move STT fully into the Celery task (async) rather than synchronous per-upload. Add a paged/filterable `GET /calls` endpoint.

### 5.12 Trim the FAQ/diarization tail
- Make `pyannote.audio` + `faster-whisper` **optional extras** (slims Docker builds; addresses known stale-image/unresolvable-requirements pain).
- `pipeline_mode=lead` makes `embed→cluster→canonicalize→memory_edges` a no-op.
- Fix the latent `*.embed → gpu.heavy` mis-route.

---

## 6. Data model changes (all additive)

- **Enums:** `CallDisposition`, `SentimentLabel` (named PG enums via `values_callable`, matching the `Intent` pattern).
- **Tables:** `call_analysis` (1:1 calls); `cdr_records`; `lead`, `agent`, `campaign`, `product`; `graph_edges` (generic). Old `memory_edges` retained for FAQ mode.
- **JSONB-first:** `calls.metadata.extra.{crux_call_id, caller_phone, agent_id}` + `calls.metadata.analysis` used for prototyping before the table migration.

---

## 7. The join (CDR-primary, transcript fallback)

1. **Primary — CDR:** `resolve(crux_call_id) → caller_phone → normalize_mobile → MOBILE_NO`. Deterministic; carries agent/queue/timestamp for the graph too.
2. **Fallback / cross-check — transcript:** extract the customer phone from the conversation, `normalize_mobile`, join. Also used to **validate** the CDR (flag mismatches).
3. `crux_call_id` is persisted at ingest so a CDR delivered later can back-fill calls already analyzed.

---

## 8. Disposition taxonomy (proposed — refine on the slice)

`CallDisposition` (inbound service):
`RESOLVED, INFO_PROVIDED, CALLBACK_REQUESTED, FOLLOW_UP_PAYMENT, COMPLAINT, ESCALATION, NOT_INTERESTED, NOT_ELIGIBLE, SERVICE_REQUEST, WRONG_NUMBER, DND, NO_RESPONSE, OTHER`

`SentimentLabel`: `POSITIVE, NEUTRAL, NEGATIVE` (+ separate `escalation: bool`, + optional per-speaker breakdown).

**Crosswalk → LMS `DISPOSITION` families** (`disposition_crosswalk.yaml`, starting point):
- `NO_RESPONSE` ← Ringing, Short Hang up, Call not connected/Beep tone, IVR tone, Call Back - Ringing
- `CALLBACK_REQUESTED` ← Call Back Later, Buy Later
- `NOT_INTERESTED` ← NI - Due to Service/Product/Price, Just Browsing
- `NOT_ELIGIBLE` ← NE - NE due to income/documents/Medical history
- `WRONG_NUMBER` ← NW - Incorrect Request, Incorrect Request, Never left name and number
- `DND` ← NW - DNC
- `SERVICE_REQUEST` ← NW - Service Request
- `FOLLOW_UP_PAYMENT` ← Follow UP for payment
- `MEETING_SCHEDULED`-like ← Meeting Scheduled (kept as `OTHER`/sales note for inbound)

Stored as new `CALL_DISPOSITION`; the LMS `DISPOSITION` column is never overwritten.

---

## 9. Knowledge graph schema

**Node types:** `lead, call, agent, campaign, product, disposition, sentiment` (optional later: `pincode/region`).

**Edge relations (`EdgeRelation` extended):**
- `lead —RECEIVED_CALL→ call`
- `call —HANDLED_BY→ agent`
- `call —HAS_DISPOSITION→ disposition`
- `call —HAS_SENTIMENT→ sentiment`
- `call —ABOUT_PRODUCT→ product`, `lead —INTERESTED_IN→ product`
- `lead —IN_CAMPAIGN→ campaign`
- `lead —SIMILAR_TO→ lead` (embedding cosine, optional)

Each edge: `weight` (confidence) + `reason` (grounded rationale), deduped on `UNIQUE(src_id, dst_id, relation)`.

---

## 10. Validation slice & gold set

- **Slice:** FY26 recent months, duration buckets `15-30s` + `30s+` (skip `<15s` noise), whose CDR caller-phone matches a `leads_canonical` row (prefer high `PRIORITY`). Target ~500–1,000 calls.
- **Gold set:** hand-label ~100 calls for: lead-field precision/recall, phone-grounding accuracy, disposition accuracy vs label, sentiment agreement.
- **Gate:** do not scale to the corpus until the slice clears agreed thresholds (set with user at P2).

---

## 11. Phasing

- **P0 — Unblock & confirm runtime.** Retain `crux_call_id` at ingest; confirm Groq + Sarvam run end-to-end on a handful (memory flags stale images / missing deps → rebuild). Obtain a CDR sample; write the CDR loader against its real columns.
- **P1 — CallAnalyzer.** Build the one-pass `{lead, disposition, sentiment}` analyzer; persist to JSONB. Port `normalize_mobile` + tests. Add enums + crosswalk.
- **P2 — Validate on the slice.** Measure against the gold set; tune prompts/taxonomy; decide go/no-go thresholds.
- **P3 — Promote to tables + API.** Additive migration for `call_analysis`; paged/filterable `GET /calls`.
- **P4 — Enrichment join.** `call_summary` exporter + CI `merge_call_intel.py` (additive, tie-break, unmatched bucket).
- **P5 — Knowledge graph.** Generic `graph_edges` + node tables + builder; `/knowledge-graph`; generalize Cytoscape viewer; Obsidian vault exporter (scoped).
- **P6 — Trim & scale.** Make pyannote/faster-whisper optional; no-op the FAQ tail on the lead path; bulk-transcribe the rest once the loop is proven.

---

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| CDR delivery slips | Transcript-phone fallback is built anyway; `crux_call_id` retained for later back-fill |
| `saaras:v3` outputs English translation → weakens verbatim phone/name grounding | Confirm Sarvam returns in-language text (or switch lead path to `saarika:v2.5` transcribe); ground against original-language utterances |
| `gpt-oss-120b` PII extraction errors pollute the lead master | Grounding guard + confidence + gold-set gate; consider stronger model for the lead field only if needed |
| Scale/cost (747k mp3, Sarvam limits, slow CPU whisper) | Slice-first; transcribe-once-store; bulk only after P2 |
| Many-to-one mobile→lead; ~7,915 leads no valid mobile | Documented tie-break + unmatched bucket; never silently drop |
| Operational fragility (stale images, deps, corp proxy) | P0 rebuild + smoke test before building on the pipeline |
| Inbound-vs-LMS disposition mismatch | Separate `CALL_DISPOSITION` column + crosswalk; don't overwrite LMS |
| Graph scale in viewer/Obsidian | Server-side aggregation/paging; scope vault to slices |

---

## 13. Testing strategy

- **TDD** for `normalize_mobile` parity (port CI test cases verbatim) — the join's correctness gate.
- `CallAnalyzer`: schema-validation, coerce-and-repair, grounding-guard (PII must trace to an utterance), map-reduce merge for long calls.
- Crosswalk: every LMS family maps to exactly one `CallDisposition`.
- Enrichment: property test asserting the merge is **additive-only** (no existing column mutated; row count preserved).
- Graph: edge dedup/upsert idempotency on `UNIQUE(src,dst,relation)`.
- Obsidian export: snapshot test of a small fixture vault (frontmatter + `[[links]]` resolve).

---

## 14. Success criteria

1. On the validation slice, lead-field extraction + phone grounding meet agreed precision thresholds; disposition accuracy and sentiment agreement clear the gate.
2. `leads_canonical` gains `CALL_*` columns with measurable fill on matched leads, **zero** mutation of existing columns.
3. `/knowledge-graph` renders typed nodes/edges in the Cytoscape viewer.
4. An Obsidian vault for a slice opens in Obsidian with correct `[[wikilinks]]` and frontmatter, and its graph view shows lead↔call↔agent↔campaign↔product structure.
5. The FAQ/diarization tail is optional and off the lead path; Docker builds slimmed.

---

## 15. Open items (decided at the phase they gate, not blockers now)

- **CDR schema:** exact columns of the Crux export (drives §5.2 config). — *needed at P0/P4*
- **Sarvam transcript language:** confirm `saaras:v3` vs `saarika:v2.5` for grounding. — *P0/P1*
- **Slice thresholds:** precision/accuracy gates for go/no-go. — *P2*
- **Tie-break rule** for many-to-one mobile→lead. — *P4*
- **Vault scope:** per-campaign vs P0-leads vs date range. — *P5*
