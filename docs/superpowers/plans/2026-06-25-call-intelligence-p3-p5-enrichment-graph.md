# Call Intelligence Phase 2 (CDR + Enrichment + Typed Graph + Obsidian) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) tracking. Designed by the `phase2-ultraplan` workflow (5 architect agents + adversarial synthesis).

**Goal:** Build the infra-free, unit-testable backbone of enrichment + knowledge graph: a config-driven Crux CDR loader, a per-mobile call-summary builder, the Campaign Intelligence additive join, an in-memory typed graph builder, and an Obsidian vault exporter.

**Architecture:** Pure functions + Pydantic/dataclass models, no DB/Celery/network/LLM. Two producer→consumer spines: (1) `CALL_SUMMARY_COLUMNS` produced by `call_summary.py` is consumed byte-identically by CI `merge_call_intel.py`; (2) the `GraphNode/GraphEdge` model defined by `graph-builder` is consumed (duck-typed) by `obsidian_export.py`. DB persistence of these (cdr_records, graph_edges tables) is a later, infra-dependent plan.

**Tech Stack:** Python 3.11, Pydantic v2, pandas (dtype=str), PyYAML, pytest. Tests via `./.venv/Scripts/python -m pytest <path> -q`.

Spec: `docs/superpowers/specs/2026-06-24-call-intelligence-enrichment-graph-design.md` (§5.2, §5.8, §5.9, §5.10, §7, §9).

## Global Constraints

- Python ≥ 3.11. Tests: `./.venv/Scripts/python -m pytest <path> -q` (never system python 3.10).
- Single-source enums in `app/models/enums.py` (`StrEnum`). All V2T unit tests live FLAT in `app/tests/unit/` (no `tests/services/...` tree).
- Phone join key: route every phone (lead-side and call-side) through `app.utils.phone.normalize_mobile` before comparison. CI side uses `src.normalize.normalize_mobile` (verbatim-identical).
- Additive only: never mutate/overwrite existing `leads_canonical` columns; appended call columns are `CALL_*`.
- Pure + fixture-tested. No DB/Celery/network/LLM in any test. CSV reads use `dtype=str`.
- TDD per task. Commit message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## CANONICAL SHARED CONTRACTS (copy verbatim — components MUST agree)

**C1 — `CALL_SUMMARY_COLUMNS`** (exact names, exact order). `call_summary.py` exports it; CI `merge_call_intel.py` redeclares the identical list as `CALL_COLUMNS`; `CALL_ATTACH_COLUMNS = CALL_COLUMNS[1:]` (PHONE_NUMBER excluded — it duplicates MOBILE_NO). All values written as STRINGS.
```python
CALL_SUMMARY_COLUMNS = [
    "PHONE_NUMBER", "CALL_DISPOSITION", "CALL_SENTIMENT", "CALL_ESCALATION",
    "CALL_N_CALLS", "CALL_LAST_DATE", "CALL_LEAD_NAME", "CALL_LEAD_EMAIL",
    "CALL_LEAD_OCCUPATION", "CALL_LEAD_INCOME_BAND", "CALL_LEAD_PRODUCT_INTEREST",
    "CALL_LEAD_PINCODE", "CALL_SOURCE_CALL_IDS", "CALL_CONFIDENCE",
]
```
- `CALL_ESCALATION` = literal `'true'`/`'false'`. `CALL_N_CALLS` = stringified int. `CALL_LAST_DATE` = `''` or ISO-8601 date (lexicographically sortable). `CALL_CONFIDENCE` = the **winning (most-significant) call's `disposition_confidence`**, rounded 3dp, stringified (NOT a mean).

**C2 — ANALYSIS dict** (input to `call_summary.py` and `graph-builder`). Exactly `app.workers.tasks._call_analysis_metadata` output:
```
{'lead': <Lead.model_dump()>, 'disposition': <CallDisposition value str>,
 'disposition_confidence': float, 'disposition_rationale': str|None,
 'sentiment': <SentimentLabel value str>, 'sentiment_confidence': float,
 'escalation': bool, 'model': str}
```
`Lead.model_dump()` keys: `full_name, phone, email, age, gender, occupation, education, income_band, pincode, product_interest, policy_no, callback_time, grounded_fields`. **Phone-grounding gate:** trust `lead['phone']` only when `'phone' in lead['grounded_fields']`.

**C3 — `CallDirection`** (add to `app/models/enums.py`): `INBOUND='inbound'`, `OUTBOUND='outbound'`, `UNKNOWN='unknown'`.

**C4 — `EdgeRelation` additions** (additive to existing cluster relations in `app/models/enums.py`):
`RECEIVED_CALL='received_call'` (lead→call), `HANDLED_BY='handled_by'` (call→agent), `HAS_DISPOSITION='has_disposition'` (call→disposition), `HAS_SENTIMENT='has_sentiment'` (call→sentiment), `ABOUT_PRODUCT='about_product'` (call→product), `INTERESTED_IN='interested_in'` (lead→product), `IN_CAMPAIGN='in_campaign'` (lead→campaign), `SIMILAR_TO='similar_to'` (lead→lead; declared, NOT emitted by the pure builder). Existing `LEADS_TO/RELATED_TO/SUBSET_OF/OPPOSES/CAUSED_BY/CO_OCCURS` untouched.

**C5 — Graph model** (`app/services/knowledge_graph/model.py`; obsidian consumes by field-name, TYPE_CHECKING import only):
```python
class NodeType(StrEnum):  # in model.py
    LEAD='lead'; CALL='call'; AGENT='agent'; CAMPAIGN='campaign'
    PRODUCT='product'; DISPOSITION='disposition'; SENTIMENT='sentiment'
# GraphNode{id:str, type:NodeType, label:str, attrs:dict}  (frozen)
# GraphEdge{src_id:str, src_type:NodeType, dst_id:str, dst_type:NodeType,
#           relation:EdgeRelation, weight:float=Field(1.0,ge=0,le=1), reason:str|None}
#   .key() -> (src_id, dst_id, relation.value)
# node_id(t: NodeType, raw: str) -> f"{t.value}:{raw}"
```

**C6 — `CallContext`** (cdr-loader): `crux_call_id:str` (required join anchor), `caller_phone:str|None` (normalized), `agent_id:str|None`, `campaign:str|None`, `started_at:datetime|None`, `direction:CallDirection=UNKNOWN`. A Crux header literally named `queue` maps via `CdrColumnMap.campaign='queue'` (config, no code change).

---

## WAVE PLAN (file-disjoint within each wave → safe for parallel agents)

- **Wave 1 (parallel):** `cdr-loader` (T1,T2 — owns the `enums.py` `CallDirection` edit) ‖ `enrichment-summary` (T3,T4 — no `enums.py` edit).
- **Wave 2 (parallel):** `ci-merge` (T5,T6 — in the SEPARATE Campaign Intelligence repo) ‖ `graph-builder` (T7,T8,T9 — owns `enums.py` `EdgeRelation/NodeType` edit + the `knowledge_graph` package incl. `__init__.py`).
- **Wave 3 (single):** `obsidian-exporter` (T10 — adds `obsidian_export.py` only; does NOT create `__init__.py`).

---

## Tasks

### T1 — cdr-loader: CallDirection + schemas
**Files:** modify `app/models/enums.py`; create `app/services/cdr/__init__.py`, `app/services/cdr/schemas.py`.
**Depends on:** —
**Impl:** Add `CallDirection(StrEnum)` (C3). In `schemas.py` (`from __future__ import annotations`), Pydantic v2 `CdrColumnMap` (logical→header, defaults `crux_call_id/caller_number/agent_id/campaign/start_time/direction`) and `CallContext` (C6); `DEFAULT_CDR_COLUMN_MAP = CdrColumnMap()`. `__init__.py` re-exports `CdrColumnMap, CallContext, CdrIndex, parse_cdr, parse_cdr_rows, build_index, DEFAULT_CDR_COLUMN_MAP`.
**Key tests** (`test_cdr_loader.py`, started here): members `{INBOUND,OUTBOUND,UNKNOWN}`; `CallContext(crux_call_id='25689211')` defaults None/UNKNOWN; missing `crux_call_id` → `ValidationError`; `DEFAULT_CDR_COLUMN_MAP` field values.

### T2 — cdr-loader: pure parsing
**Files:** create `app/services/cdr/loader.py`, `app/tests/unit/test_cdr_loader.py`.
**Depends on:** T1
**Impl:** `row_to_context(row, column_map)` uses `row.get(column_map.<field>)` (tolerate absent headers); `caller_phone=normalize_mobile(raw)`, `agent_id/campaign=clean_na(raw)`, `crux_call_id=clean_na(raw)`, `started_at=_parse_started_at(raw)`, `direction=_parse_direction(raw)`. `_parse_started_at`: `clean_na` → `datetime.fromisoformat`, else loop a small `strptime` FORMATS tuple, else None. `_parse_direction`: lower; `_INBOUND={'in','inbound','mt'}`, `_OUTBOUND={'out','outbound','mo'}`, else UNKNOWN. `parse_cdr_rows` drops rows with empty `crux_call_id`. `parse_cdr(source, column_map)`: `pd.read_csv(source, dtype=str, keep_default_na=False)` → `to_dict('records')` → `parse_cdr_rows`. `build_index` → `CdrIndex(dict[crux_call_id]=ctx)` last-wins; `resolve(id)`/`__len__`/`__contains__`.
**Key tests:** default-header map; **phone parity** (`caller_phone == normalize_mobile(raw)` over +91/0/91/.0 table); landline → `caller_phone=None` but record kept; `NA`/`#N/A` → None; direction MO→OUTBOUND, MT→INBOUND, ''→UNKNOWN; `_parse_started_at` ISO/space/None/garbage; custom `CdrColumnMap(crux_call_id='CallID', caller_phone='ANI')`; StringIO vs tmp-file parity (leading-zero phone preserved); last-wins dedupe; `resolve` hit/miss/None.

### T3 — enrichment-summary: collapse helpers
**Files:** create `app/services/enrichment/call_summary.py`.
**Depends on:** —
**Impl:** `DISPOSITION_SIGNIFICANCE` ranking (most→least): `escalation, complaint, follow_up_payment, callback_requested, service_request, not_eligible, not_interested, dnd, wrong_number, resolved, info_provided, no_response, other`. `_record_phone(rec)`: `cand = rec.phone if normalize_mobile(rec.phone) else (lead['phone'] if 'phone' in lead.get('grounded_fields',[]) else None)`; return `normalize_mobile(cand)`. `_disposition_rank(d)` → index or `len(...)`. `_collapse_disposition(records)` → min over key `(rank, -date_key, -conf)`; returns `(disposition, winning_confidence)`. `_first_non_null(records, field)` scans most-recent-first (date desc, call_id asc), skips ''/None.
**Key tests** (in T4 file): grounding gate (ungrounded `lead.phone` → None; CDR override wins; CDR junk → grounded fallback); rank ordering; collapse significance-beats-confidence, newer breaks same-disposition tie, conf breaks remaining tie; `_first_non_null` scan + tie-break.

### T4 — enrichment-summary: build + write
**Files:** modify `app/services/enrichment/call_summary.py`; create `app/tests/unit/test_call_summary.py`.
**Depends on:** T3
**Impl:** Define a small `CallRecord` input (fields: `call_id:str`, `call_date:str` ISO `YYYY-MM-DD`, `phone:str|None` [CDR phone], `analysis:dict` [C2]). `build_call_summary(records) -> list[dict]`: group by `_record_phone` (drop unresolved); per phone — `disposition,conf=_collapse_disposition(g)`; `CALL_SENTIMENT` from the SAME winning record; `CALL_ESCALATION='true' if any(...) else 'false'`; `CALL_N_CALLS=str(len(g))`; `CALL_LAST_DATE=max(non-null dates, default '')`; lead fields via `_first_non_null`; `CALL_SOURCE_CALL_IDS` comma-join (date desc, call_id); `CALL_CONFIDENCE=str(round(conf,3))` (winning conf, per C1). Assemble exactly `CALL_SUMMARY_COLUMNS`; return sorted by `PHONE_NUMBER`. `write_call_summary(rows, path)`: `csv.DictWriter(fieldnames=CALL_SUMMARY_COLUMNS, extrasaction='ignore')`, None→''.
**Key tests:** 3 records → 2 rows sorted; escalation OR; `CALL_CONFIDENCE`==winning conf (e.g. winner .4 among [.4,.6] → `'0.4'`); `set(row)==set(CALL_SUMMARY_COLUMNS)`; unresolved-phone record excluded; `write_call_summary` round-trips via `read_csv(dtype=str)` (column order, leading digit kept, missing→'' not 'None', `[]`→header-only).

### T5 — ci-merge: constants + imports  (Campaign Intelligence repo)
**Files:** create `C:/Users/Diwakar.Adhikari01/Desktop/Campaign Intelligence/src/merge_call_intel.py`.
**Depends on:** T4
**Impl:** `CALL_COLUMNS` = the exact 14-name list from C1; `CALL_ATTACH_COLUMNS = CALL_COLUMNS[1:]`. `from src.normalize import normalize_mobile, clean_na`. `HERE/MARTS/OUTPUT` path constants mirroring `merge_leads_activity.py`. `_is_valid_flag(series)`: `series.astype(str).str.strip().str.lower().isin({'true','1','yes','y','t'})`.
**Key tests** (in T6 file): `CALL_COLUMNS` byte-identical to T4; `CALL_ATTACH_COLUMNS==CALL_COLUMNS[1:]`; `_is_valid_flag` truthiness table; imports resolve.

### T6 — ci-merge: additive join  (Campaign Intelligence repo)
**Files:** modify `.../src/merge_call_intel.py`; create `.../tests/test_merge_call_intel.py`.
**Depends on:** T5
**Impl:** `collapse_duplicate_calls(call_summary)`: `_norm_phone=PHONE_NUMBER.map(normalize_mobile)` (drop None); `_dt=to_datetime(CALL_LAST_DATE,errors='coerce')`, `_n=to_numeric(CALL_N_CALLS).fillna(-1)`, `_c=to_numeric(CALL_CONFIDENCE).fillna(-1)`; `sort_values(['_norm_phone','_dt','_n','_c','CALL_SOURCE_CALL_IDS'], ascending=[T,F,F,F,T], na_position='last')` → `drop_duplicates('_norm_phone', keep='first')`; write normalized value back to `PHONE_NUMBER`; return `CALL_COLUMNS` subset. `merge_call_intel(leads, call_summary, mobile_col='MOBILE_NO', valid_col='MOBILE_VALID')`: `_k=leads[mobile_col].map(normalize_mobile)`, null `_k` where `~_is_valid_flag`; `calls=collapse_duplicate_calls(...)` keyed `_k=PHONE_NUMBER`; left-join `leads[['_k']]` → calls on `_k`; `out=leads.copy()`; set each `CALL_ATTACH_COLUMNS` col from merged `.values` (never touch existing cols). `find_unmatched_calls(leads, call_summary, ...)`: `valid_keys` = leads `_k` (valid & notnull); `mask=~collapsed.PHONE_NUMBER.isin(valid_keys)`; return `[CALL_COLUMNS]`. `run_merge(leads_path, calls_path, out_path, unmatched_path)`: read dtype=str, merge+find, `makedirs`, `to_csv(index=False)` guarded `try/except PermissionError`, return stats dict `{lead_rows, call_rows, matched_leads, unmatched_calls, added_columns}`.
**Key tests:** additivity (`len(out)==len(leads)`; `out.columns[:n]==leads.columns` byte-identical via `assert_frame_equal` on the lead slice; appended == `CALL_ATTACH_COLUMNS`); many-to-one (3 leads same mobile + 1 call → all 3 enriched, count 3, no cross-product); `MOBILE_VALID=false` lead forced NaN even on phone collision; tie-break (most-recent date, then N desc, then conf desc, then source asc; dated beats blank); `find_unmatched_calls` buckets phones with no valid lead; normalization parity (`+91 98765-43210` matches `9876543210`); `run_merge` IO on tmp_path (22+13 cols, first 22 identical on disk, stats correct).

### T7 — graph-builder: EdgeRelation additions
**Files:** modify `app/models/enums.py`.
**Depends on:** T1 (so the `CallDirection` edit already landed; this is the sole pending enums edit in Wave 2)
**Impl:** Append the 8 members (C4) to `EdgeRelation` with inline `# src->dst` comments; update class docstring to distinguish cluster vs entity relations.
**Key tests** (`test_graph_model.py`, started here): 8 new members with snake_case values; original 6 cluster relations still present; `EdgeRelation('has_disposition')` round-trips.

### T8 — graph-builder: model
**Files:** create `app/services/knowledge_graph/__init__.py`, `app/services/knowledge_graph/model.py`, `app/tests/unit/test_graph_model.py`.
**Depends on:** T7
**Impl:** `model.py` (C5): `NodeType(StrEnum)`; `GraphNode` (frozen) / `GraphEdge` Pydantic v2 (`weight: float = Field(1.0, ge=0, le=1)`, `.key()`); module `node_id(t, raw)`. `TypedGraph`: id→node and key→edge indexes; `add_node` dedup (merge attrs `{**old, **{k:v for k,v in new if v is not None}}`, keep first non-empty label); `add_edge` dedup by key (replace only if `new.weight>old.weight`, taking its reason); `merge(other)`; expose `.nodes`/`.edges` lists. `__init__.py` re-exports the model + `build_call_graph`/`merge_graphs` (lazy import ok).
**Key tests:** `node_id(LEAD,'9876543210')=='lead:9876543210'`; `node_id(DISPOSITION, COMPLAINT.value)=='disposition:complaint'`; `GraphEdge.key()`; `weight=1.5`→`ValidationError`; `add_node` dedup/label/attrs-merge; `add_edge` dedup higher-weight-wins; `merge`.

### T9 — graph-builder: build
**Files:** create `app/services/knowledge_graph/build.py`, `app/tests/unit/test_graph_build.py`.
**Depends on:** T8
**Impl:** `build_call_graph(analysis: dict, *, call_id: str, call_date: str|None=None, cdr: CallContext|None=None, lead_rows: list[dict]|None=None) -> TypedGraph`. Resolve join phone: `cdr` present → `normalize_mobile(cdr.caller_phone) or grounded lead.phone`; else grounded `lead.phone`. Always add CALL node (anchor; attrs include `escalation`, and `phone_mismatch=True` when CDR phone and grounded lead phone normalize to different non-None values). If phone → LEAD node `lead:<phone>` (attrs `lead_nos`=matched `LEAD_NO`s, `matched`=bool), `RECEIVED_CALL` (1.0). DISPOSITION/SENTIMENT nodes from enum `.value`; `HAS_DISPOSITION` (weight=`clamp(disposition_confidence)`, reason=`disposition_rationale`), `HAS_SENTIMENT` (weight=`clamp(sentiment_confidence)`). AGENT node + `HANDLED_BY` only if `clean_na(cdr.agent_id)`. CAMPAIGN node + `IN_CAMPAIGN` only if `clean_na(cdr.campaign)`. PRODUCT nodes = union of normalized `lead.product_interest` + matched rows' `PRODUCT_TYPE`; `ABOUT_PRODUCT` (call→product) + `INTERESTED_IN` (lead→product). All edges via `TypedGraph.add_edge` with clamped weight (no ValidationError). `merge_graphs(*graphs)`.
**Key tests:** CDR-primary happy path (lead node `lead_nos==['L1','L2']`, `matched==True`, all node/edge types, `HAS_DISPOSITION.weight==conf` & `reason==rationale`, call `escalation==True`); transcript fallback (no cdr, grounded phone → lead + RECEIVED_CALL, no agent/campaign); CDR junk → fallback + `phone_mismatch`; no lead match → `matched==False`, `lead_nos==[]`; `agent_id 'NA'`/`campaign None` → no node/edge; no phone anywhere → no lead node but call+disposition+sentiment + edges; upstream `weight>1.0` clamped; `merge_graphs` (2 calls same lead → 1 lead, 2 call nodes, 2 RECEIVED_CALL; idempotent; empty → empty).

### T10 — obsidian-exporter
**Files:** create `app/services/knowledge_graph/obsidian_export.py`, `app/tests/unit/test_obsidian_export.py`.
**Depends on:** T9 (graph model exists; import under `TYPE_CHECKING` only, duck-type at runtime)
**Impl:** `_SLUG_RE=re.compile(r'[^a-z0-9]+')`; `slugify(s)` → lower, sub, `strip('-')` or `'untitled'`; `note_name(type,id)=f"{slugify(type)}-{slugify(id)}"`; `TYPE_FOLDERS={lead:'leads',call:'calls',agent:'agents',campaign:'campaigns',product:'products',disposition:'dispositions',sentiment:'sentiments'}`, `folder_for(t)=TYPE_FOLDERS.get(str(t),'misc')`. `_flatten_attrs`: StrEnum/Enum→`.value`; scalars pass; list/tuple→`', '.join`; dict→`json.dumps(sorted)`; else `str`. `render_frontmatter(node)`: `data={'type':node.type,'id':node.id,'label':node.label}` + sorted flattened attrs → `yaml.safe_dump(sort_keys=False, allow_unicode=True)` fenced `---`. `_related(node, edges)`: outgoing (src matches) + incoming (dst matches), dedupe `(relation, other_note_name, direction)`, sort. `render_body`: `# {label}` + `## Related` + table `| Relation | Linked Note | Weight | Reason |` with `[[note_name]]`. `write_vault(nodes, edges, out_dir)`: in-batch slug-collision map → append `'-'+sha1(f'{type}|{id}')[:6]`; mkdir folders; `write_text(encoding='utf-8')`; return sorted paths.
**Key tests:** `slugify` cases (`'Call 2026-06: A/B'`→`'call-2026-06-a-b'`; ''→'untitled'; `'A___B--C'`→`'a-b-c'`); `_flatten_attrs` (StrEnum→value, list join, nested dict→string no raise); `render_frontmatter` (`yaml.safe_load(inner)` equals data; Devanagari survives; deterministic); `render_body/_related` (bidirectional, deduped, self-loop once, dangling edge still `[[...]]`); `write_vault` on tmp_path (folder layout; frontmatter parses; in-graph wikilinks resolve to emitted basenames; unknown type→`misc/`; `[]`→no folders; colliding ids → distinct sha-suffixed files; idempotent).

---

## Self-Review

- **Spec coverage:** T1–T2 → §5.2/§7 (CDR + join); T3–T4 → §5.8 (call_summary producer); T5–T6 → §5.8 (CI additive join); T7–T9 → §5.9/§9 (typed graph); T10 → §5.10 Option B (Obsidian). DB persistence (cdr_records, graph_edges tables), `/knowledge-graph` API, Cytoscape generalization, and table promotion remain explicitly OUT (infra-dependent; later plans).
- **Placeholder scan:** none — every task has files, signatures, impl sketch, and concrete test cases.
- **Type consistency:** `CALL_SUMMARY_COLUMNS`(C1) == CI `CALL_COLUMNS`; ANALYSIS dict(C2) matches `_call_analysis_metadata`; `GraphNode/GraphEdge`(C5) field-name set consumed by obsidian; `CALL_CONFIDENCE` defined once (winning conf). `node_id`/`.key()` used consistently across T8/T9/T10.
- **Parallel safety:** Wave 1 — only cdr-loader edits `enums.py`. Wave 2 — graph-builder edits `enums.py` (after Wave 1 committed) + owns `knowledge_graph/__init__.py`; ci-merge is a different repo. Wave 3 — obsidian adds one file. Disjoint confirmed.
