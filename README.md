# v2t — Voice-to-Text Insurance Call Intelligence

> Multilingual insurance call intelligence platform.
> Audio in → searchable semantic memory + FAQ clusters + memory graph out.

[![Repo](https://img.shields.io/badge/repo-Thunderk3g%2Fvoice--to--text-181717?logo=github)](https://github.com/Thunderk3g/voice-to-text)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000?logo=next.js)
![Postgres](https://img.shields.io/badge/Postgres-16%2Bpgvector-336791?logo=postgresql&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-OpenAI--compatible%20(Groq%20default)-7B4DBA)
![Embeddings](https://img.shields.io/badge/Embeddings-Cohere%20%2F%20e5--large-39A0ED)
![Sarvam](https://img.shields.io/badge/STT-Sarvam.ai-F26B38)

---

## What this is

A production-grade pipeline that ingests Indian insurance customer-support
calls — either raw audio or pre-labeled transcripts — and produces:

1. Structured customer intents (14 closed-set labels)
2. Cross-lingual semantic memory in pgvector
3. Auto-clustered FAQs with canonical question + suggested answer
4. A cluster-to-cluster **memory graph** rendered in the dashboard with Cytoscape.js
5. Retrieval APIs (`/search`, `/cluster/{id}`, `/memory-graph`, `/analytics`)
6. Drift detection for emerging customer concerns
7. Human feedback loop (merge / split / relabel / regenerate)

Languages supported end-to-end: **Hindi, English, Hinglish, Roman-Hindi, Tamil, Telugu** —
plus Sarvam's broader Indic set when STT is enabled.

## Provider stack

| Layer        | Provider                            | Why                                                     |
|--------------|--------------------------------------|---------------------------------------------------------|
| **STT**      | [Sarvam.ai](https://www.sarvam.ai/) | Hindi-native (no Whisper-to-Urdu drift), 10 Indic langs |
| **LLM**      | Any OpenAI-compatible `/v1` endpoint — **[Groq](https://groq.com/) `openai/gpt-oss-120b` by default**; [Ollama](https://ollama.com/), vLLM, or OpenAI all work | One client, swap provider with `LLM_BASE_URL` / `LLM_MODEL` — no code change |
| **Embeddings** | **[Cohere](https://cohere.com/) `embed-multilingual-v3.0` by default** (hosted, no torch), or local `intfloat/multilingual-e5-large` | Both 1024-d → identical pgvector schema; switch with `EMBEDDING_PROVIDER` |
| **Vector DB**  | Postgres 16 + `pgvector`           | One database, ivfflat → HNSW on scale-up                 |
| **Queue**    | Celery + Redis                      | At-least-once with `task_acks_late=True`                 |
| **Storage**  | MinIO (S3-compatible)               | Raw audio, transcript JSON, pipeline artifacts           |
| **UI**       | Next.js 14 (App Router) + Cytoscape.js + Plotly | Cluster explorer, memory graph, retrieval playground   |

The default stack is **fully hosted for LLM + embeddings (Groq + Cohere), so no
GPU is required**. Point `LLM_BASE_URL` at a local Ollama and set
`EMBEDDING_PROVIDER=local` to run everything on-prem instead.

Audio → Sarvam (chunked on silences, <30 s each, transcribed in parallel) →
heuristic speaker assignment → LLM JSON-mode extraction → embedding (Cohere or
e5) → HDBSCAN + incremental assignment → LLM canonicalization + memory edges.
Pre-labeled transcript JSON can be ingested directly, skipping the STT stage.

See [`docs/architecture.md`](docs/architecture.md) for the full diagram.

---

## Quickstart

The default direction is **open-source-first**: a native Ollama LLM, local e5
embeddings on CPU, and open-source faster-whisper STT — **no paid API keys and
no GPU required**. Two ready-made profiles are provided:

- **`.env.opensource`** — fully open source (faster-whisper STT on CPU). No keys.
- **`.env.sarvam`** — high-accuracy Hindi/Indic STT via Sarvam (set `SARVAM_API_KEY`).

Both run the LLM via a **native Ollama on the host**, reached from containers
via `host.docker.internal`. (See [Run on macOS](#run-on-macos-apple-silicon--m-series)
for the Apple-Silicon walkthrough.)

```bash
git clone https://github.com/Thunderk3g/voice-to-text.git
cd voice-to-text

# Run Ollama natively on the host first (Metal-accelerated on Mac):
ollama serve &
ollama pull qwen2.5:7b-instruct

# Pick a profile:
cp .env.opensource .env       # fully open source (Whisper STT)
# cp .env.sarvam .env         # Sarvam STT — then set SARVAM_API_KEY in .env

docker compose up -d --build  # worker-gpu is opt-in via --profile gpu

# Seed the 6 sample multilingual transcripts:
python -m app.scripts.seed_data --api-url http://localhost:8080
```

> To use a hosted LLM (Groq) instead of Ollama, uncomment the Groq block in
> your `.env`.

Open:

| URL                          | What                                             |
|------------------------------|--------------------------------------------------|
| http://localhost:3001        | Next.js dashboard (cluster explorer, graph, …)   |
| http://localhost:8080/docs   | FastAPI Swagger                                  |
| http://localhost:8080/metrics| Prometheus exposition                            |
| http://localhost:5555        | Flower (Celery queue monitor)                    |
| http://localhost:9001        | MinIO console                                    |

---

## Run on macOS (Apple Silicon / M-series)

On a MacBook (M-series), Docker Desktop **can't pass the Apple GPU through to
containers**, so the LLM runs **natively on the host** (Metal-accelerated) and
the containers reach it over `host.docker.internal`. The CUDA `worker-gpu`
container can't run on Mac, so it's opt-in and the CPU worker handles
embeddings.

**1. Install and run Ollama natively** (not in Docker):

```bash
brew install ollama            # or download from https://ollama.com/download
ollama serve &                 # Metal-accelerated; listens on :11434
ollama pull qwen2.5:7b-instruct
```

Containers reach this host Ollama via `LLM_BASE_URL=http://host.docker.internal:11434/v1`
(already set in both profile files; compose adds the `host.docker.internal`
host entry so it works on Linux too).

**2. Pick a profile:**

```bash
cp .env.opensource .env        # fully open source — faster-whisper STT, no keys
# or:
cp .env.sarvam .env            # Sarvam STT — then set SARVAM_API_KEY in .env
```

**3. Bring up the stack** (no GPU needed; `worker-gpu` is opt-in):

```bash
docker compose up -d --build
# NVIDIA/Linux users who want the CUDA embedding worker instead:
#   docker compose --profile gpu up -d --build
```

**4. Seed the sample transcripts:**

```bash
python -m app.scripts.seed_data --api-url http://localhost:8080
```

Then open the dashboard at **http://localhost:3001** (compose maps 3001 → the
container's 3000).

**Accuracy vs. speed tradeoff:** the open-source profile's faster-whisper
`large-v3` runs on CPU — it's noticeably **slower** and **weaker on
Hindi/Devanagari** than Sarvam. If high-accuracy Indic audio is the priority,
use the **sarvam** profile (the high-accuracy audio path); the **opensource**
profile is the zero-key, fully on-prem path.

---

## Sample API calls

Ingest an **audio** call (Sarvam transcribes it):

```bash
curl -X POST http://localhost:8080/ingest \
  -H 'Content-Type: application/json' \
  -d '{
        "source_uri": "minio://audio-raw/call_2026_05_27_001.wav",
        "is_transcript": false,
        "metadata": {"campaign": "renewal_q1", "channel": "outbound"}
      }'
```

Ingest a **pre-labeled transcript** (Sarvam Batch already done, or any other
external pipeline):

```bash
curl -X POST http://localhost:8080/ingest \
  -H 'Content-Type: application/json' \
  -d '{
        "source_uri": "file:///workspace/data/sample_transcripts/call_001_hindi.json",
        "is_transcript": true,
        "metadata": {"campaign": "seed"}
      }'
```

Semantic search (Hinglish query):

```bash
curl -X POST http://localhost:8080/search \
  -H 'Content-Type: application/json' \
  -d '{ "query": "claim reject kyun hua", "top_k": 5 }'
```

Pull the memory graph for the dashboard:

```bash
curl 'http://localhost:8080/memory-graph?min_weight=0.6'
```

Regenerate a cluster's canonical FAQ (human feedback action):

```bash
curl -X POST http://localhost:8080/feedback \
  -H 'Content-Type: application/json' \
  -d '{ "action": "regenerate_faq", "payload": {"cluster_id": "<uuid>"} }'
```

---

## Services

| Service              | Image                          | GPU | Port       |
|----------------------|--------------------------------|-----|------------|
| `api`                | python:3.11, FastAPI           | no  | 8080       |
| `worker-cpu`         | python:3.11                    | no  | —          |
| `worker-gpu`         | nvidia/cuda + python:3.11      | opt | —          |
| `postgres`           | pgvector/pgvector:pg16         | no  | 5432       |
| `redis`              | redis:7-alpine                 | no  | 6379       |
| `minio`              | minio/minio                    | no  | 9000, 9001 |
| `flower`             | mher/flower                    | no  | 5555       |
| `frontend`           | node:20 (Next.js 14 standalone)| no  | 3001 → 3000|
| `beat`               | python:3.11                    | no  | —          |

`worker-gpu` only does work when `EMBEDDING_PROVIDER=local`; with the default
hosted Cohere embeddings the entire fleet is CPU-only.

The **LLM, embeddings, and STT providers are not containers** — Groq, Cohere,
and Sarvam.ai are reached over their public HTTPS APIs. Only API keys are
needed. (Run a local Ollama container and point `LLM_BASE_URL` at it if you
prefer on-prem inference.)

---

## GPU sizing (on-prem / hosted-free deployments only)

The default Groq + Cohere stack needs **no GPU**. The numbers below apply only
if you self-host the models — e.g. `EMBEDDING_PROVIDER=local` and/or a local
Ollama LLM — on a single RTX 4080 (16 GB):

| Workload                          | VRAM          | Notes                              |
|-----------------------------------|---------------|------------------------------------|
| Ollama `qwen2.5:7b-instruct` q4_K_M | ~5–6 GB     | Only if you self-host the LLM      |
| multilingual-e5-large             | ~2 GB         | Only when `EMBEDDING_PROVIDER=local`; loaded once per worker-gpu |
| Headroom                          | ~8 GB         | OS + fragmentation + future models |

Smaller-card profiles (RTX 4070 Ti 12 GB, RTX 3060 8 GB, A10 24 GB+) and
tuning knobs live in [`docs/gpu_sizing.md`](docs/gpu_sizing.md).

---

## Local dev without Docker

```bash
python -m venv .venv && source .venv/bin/activate     # or .\.venv\Scripts\activate on Windows
pip install -r requirements.txt

cp .env.example .env                                  # tweak DSN to localhost

# Stand up just the data plane via Docker:
docker compose up -d postgres redis minio
alembic upgrade head

# LLM + embeddings are hosted by default (Groq + Cohere) — just set the keys
# in .env, nothing to install. To run the LLM on-prem instead, install Ollama
# (https://ollama.com/download), `ollama serve &`, `ollama pull qwen2.5:7b-instruct`,
# and point LLM_BASE_URL at http://localhost:11434/v1.

# Then run the app processes:
uvicorn app.api.main:app --reload --port 8080
celery -A app.workers.celery_app worker --loglevel=INFO -Q celery
celery -A app.workers.celery_app worker --loglevel=INFO -Q gpu.heavy --concurrency=1
celery -A app.workers.celery_app beat   --loglevel=INFO

# Frontend:
cd frontend && npm install && npm run dev
```

## Running tests

```bash
pytest app/tests/unit -v              # always works
pytest app/tests/integration -v       # auto-skipped if DATABASE_URL_SYNC unreachable
```

---

## Troubleshooting

- **`SarvamConfigError: SARVAM_API_KEY is not set`** — set `SARVAM_API_KEY` in `.env`
  and rebuild the workers, or switch `STT_PROVIDER=none` and ingest pre-labeled
  transcripts only.
- **`ollama pull` hangs** — model layers are big; first pull is ~4–5 GB. Watch
  `docker logs v2t-ollama` for progress.
- **`worker-gpu` won't start on Windows** — GPU passthrough in docker-compose
  needs a Linux host. Run under WSL2 with NVIDIA Container Toolkit installed.
- **Sample-data ingest fails with `file://` path** — file paths are read by the
  worker container; mount the repo into the worker, or use
  `--minio` on `seed_data.py` to upload transcripts to MinIO first.
- **MinIO buckets missing** — the `minio-setup` one-shot creates them on first
  `docker compose up`. Re-run it manually with
  `docker compose up minio-setup` if needed.
- **Hindi transcript looks like Arabic script** — that's the Whisper-on-Hindi
  drift symptom. This stack uses Sarvam (Devanagari-native) precisely to avoid
  it; double-check that `STT_PROVIDER=sarvam` and an API key is set.

---

## Roadmap

- [x] Pluggable LLM backend behind one OpenAI-compatible interface (Groq /
      Ollama / vLLM / OpenAI — swap via `LLM_BASE_URL`)
- [x] Pluggable embedding backend (hosted Cohere or local e5, `EMBEDDING_PROVIDER`)
- [ ] Switch pgvector index from ivfflat to HNSW once corpus passes ~1 L vectors
- [ ] Wire Sarvam **Batch STT API** (returns proper diarization) to replace the
      on-board speaker heuristic
- [ ] OpenTelemetry collector container wiring (config in `docs/deployment.md`)
- [ ] Pre-built Grafana dashboard for the v2t Prometheus namespace

---

## Project layout

```
app/
  api/             FastAPI routes + dependencies
  workers/         Celery tasks + pipelines + sync DB glue
  services/
    stt/           Sarvam client + transcript loader + speaker heuristic
    llm/           OpenAI-compatible LLM client (Groq / Ollama / vLLM / OpenAI)
    extraction/    LLM-driven question extractor
    embedding/     Cohere + local e5-large encoders + Redis cache
    canonicalization/  Per-cluster FAQ synthesis
    memory_graph/  LLM-inferred cluster-to-cluster edges
    factories.py   Glue between sync workers and async services
  clustering/      HDBSCAN + incremental + drift
  db/              SQLAlchemy 2.0 models + Alembic migrations
  models/          Pydantic schemas + closed-set enums
  prompts/         Master extraction / canonicalization / relation prompts
  utils/           lang detect, vector math, robust JSON parsing
  scripts/         seed_data, benchmark
  tests/           unit + integration

frontend/          Next.js 14 dashboard (App Router, Cytoscape, Plotly)
data/sample_transcripts/  6 hand-authored multilingual call JSONs
docs/              architecture, contracts, deployment, gpu_sizing
infra/postgres/    Dockerfile with pgvector + pgcrypto extensions
```

---

## License

MIT. See [`LICENSE`](LICENSE) for the full text.
