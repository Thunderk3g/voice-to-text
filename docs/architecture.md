# v2t — Architecture

## Purpose

A multilingual insurance call intelligence platform that ingests
speaker-labeled transcripts of Indian customer support calls (produced by
**Servo AI** upstream) and produces:

1. Structured customer intents
2. Searchable semantic memory (pgvector)
3. FAQ clusters with canonical questions/answers
4. A cluster-to-cluster memory graph
5. Retrieval APIs
6. Analytics + drift detection
7. Human feedback corrections that feed back into the graph

All services are containerized via `docker-compose.yml`.

## Scope boundary — what this platform does NOT do

- **Speech-to-text** and **speaker diarization** are handled by Servo AI
  before transcripts reach this system. No `faster-whisper`, no
  `pyannote.audio`, no audio processing on our side.
- Audio files are not ingested; transcript JSON is.

## High-level flow

```
                ┌──────────────┐
   Servo-AI JSON ─▶│  /ingest API │──┐
                └──────────────┘  │
                                  ▼
                            ┌──────────┐
                            │  Celery  │  task queue (Redis)
                            └────┬─────┘
                                 │
   ┌────────────────────────────────────────────────────────────┐
   │  Pipeline stages (each a Celery task)                       │
   │                                                             │
   │   _load_transcript ─▶ Extraction (Ollama)                   │
   │   (Servo-AI JSON →     (customer Qs + intents,              │
   │    utterances table)    strict JSON output)                 │
   │                                          │                  │
   │                                          ▼                  │
   │                                     Embedding (e5-large)    │
   │                                          │                  │
   │                                          ▼                  │
   │                                  Clustering (HDBSCAN +      │
   │                                  incremental assignment)    │
   │                                          │                  │
   │                                          ▼                  │
   │                              FAQ canonicalization (Ollama)  │
   │                                          │                  │
   │                                          ▼                  │
   │                              Memory edge inference (Ollama) │
   └────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────┐
                │   PostgreSQL + pgvector      │
                │   MinIO (transcripts + arts) │
                └──────────────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────┐
                │   FastAPI retrieval API      │
                │   /search /cluster /faq      │
                │   /memory-graph /analytics   │
                │   /feedback                  │
                └──────────────────────────────┘
                                 │
                                 ▼
                ┌──────────────────────────────┐
                │   Next.js dashboard          │
                │   - Cluster Explorer         │
                │   - Memory Graph (Cytoscape) │
                │   - Retrieval Playground     │
                │   - Call Inspector           │
                │   - Drift Detection          │
                └──────────────────────────────┘
```

## Services (each container)

| Service              | Image / Runtime                | GPU | Role                                            |
|----------------------|--------------------------------|-----|-------------------------------------------------|
| `api`                | python:3.11, FastAPI           | no  | REST API, ingest, retrieval                      |
| `worker-cpu`         | python:3.11                    | no  | Extraction (HTTP → Ollama), cluster, canon, edges, feedback |
| `worker-gpu`         | python:3.11 + CUDA             | yes | Embedding only (e5-large)                        |
| `ollama`             | ollama/ollama:latest           | yes | qwen2.5:7b-instruct, OpenAI-compatible HTTP      |
| `postgres`           | pgvector/pgvector:pg16         | no  | Primary store w/ vector index                    |
| `redis`              | redis:7                        | no  | Celery broker + result backend + embedding cache |
| `minio`              | minio/minio                    | no  | Object storage (transcript JSON + artifacts)     |
| `flower`             | mher/flower                    | no  | Celery monitoring                                |
| `frontend`           | node:20, Next.js 14            | no  | Dashboard UI                                     |
| `beat`               | python:3.11                    | no  | Celery beat scheduler                            |
| `otel-collector` *opt* | otel/opentelemetry-collector | no  | Tracing/metrics export                           |

## Data model (9 tables)

1. `calls` — one row per ingested call (always a Servo-AI transcript)
2. `utterances` — speaker-labeled diarized segments (loaded from Servo-AI JSON)
3. `extracted_questions` — LLM-derived customer questions
4. `embeddings` — pgvector(1024) rows, FK to questions
5. `semantic_clusters` — cluster metadata + centroid
6. `cluster_members` — many-to-many: question ↔ cluster
7. `canonical_faqs` — versioned canonical FAQ per cluster
8. `memory_edges` — directed edges between clusters
9. `feedback_annotations` — human corrections (merge/split/relabel)

Vector index: `ivfflat` on `embeddings.vector` using cosine; switch to
`hnsw` once corpus exceeds ~1L vectors.

## Multilingual handling

- Servo AI provides per-segment language labels; we preserve them.
- Roman-Hindi & Hinglish are detected via heuristic in `app/utils/lang.py`
  as a fallback when Servo's label is missing.
- Embeddings use multilingual-e5-large (cross-lingual aligned space).
- Extraction prompt preserves the original language; English gloss is added
  to enable cross-lingual retrieval without losing the source text.

## GPU sizing — single RTX 4080 (16 GB)

| Workload                      | Footprint       | Notes                                  |
|-------------------------------|------------------|----------------------------------------|
| Ollama Qwen2.5-7B-Instruct    | ~5–6 GB         | q4_K_M default; bumps to ~8 GB at q5  |
| multilingual-e5-large         | ~2 GB           | Loaded once per worker-gpu process    |
| Headroom                      | ~8 GB           | OS + fragmentation + future models    |

Recommended runtime layout:
- `ollama` container with `--gpus all`. Pre-pull the model via the
  `ollama-init` one-shot service.
- `worker-gpu` runs sentence-transformers e5-large. Concurrency=1 keeps
  VRAM predictable.

## Scaling notes

- For >1 lakh questions: stand up a second `worker-gpu` per additional GPU;
  Celery's `gpu.heavy` queue partitions embedding work cleanly.
- Postgres: enable async commits + raise `maintenance_work_mem` before
  building the ivfflat index. At 1L+ vectors, migrate to HNSW.
- Embedding cache: Redis-backed, keyed by `sha1(prefixed_text):{model}`.
- Incremental clustering: assign new embeddings to nearest centroid if cosine
  ≥ `CLUSTER_INCREMENTAL_THRESHOLD`; otherwise queue for next batch HDBSCAN.

## Observability

- Structured JSON logs (`structlog`) in non-local environments
- Prometheus counters per pipeline stage (`v2t_*` namespace)
- OpenTelemetry tracing through FastAPI + Celery + SQLAlchemy when an OTLP
  endpoint is configured

## Human feedback loop

`POST /feedback` accepts five actions:
- `merge_clusters` — rewrite `cluster_members` from A∪B → B; deactivate A
- `split_cluster` — rerun HDBSCAN on members of one cluster
- `relabel_intent` — bulk update intent for a cluster's questions
- `regenerate_faq` — invalidate canonical FAQ and re-run canonicalization
- `reassign_question` — move a single question to another cluster

All actions are appended to `feedback_annotations` so the corrections can be
replayed in evaluation/training pipelines.
