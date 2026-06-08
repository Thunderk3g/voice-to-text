# v2t — Voice-to-Text Insurance Call Intelligence

> Multilingual insurance call intelligence platform.
> Audio in → searchable semantic memory + FAQ clusters + memory graph out.

[![Repo](https://img.shields.io/badge/repo-Thunderk3g%2Fvoice--to--text-181717?logo=github)](https://github.com/Thunderk3g/voice-to-text)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000?logo=next.js)
![Postgres](https://img.shields.io/badge/Postgres-16%2Bpgvector-336791?logo=postgresql&logoColor=white)
![LLM](https://img.shields.io/badge/LLM-OpenAI--compatible%20(Ollama%20default)-7B4DBA)
![Embeddings](https://img.shields.io/badge/Embeddings-local%20e5--large%20%2F%20Cohere-39A0ED)
![STT](https://img.shields.io/badge/STT-Whisper%20%2F%20Sarvam.ai-F26B38)

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
| **STT**      | Open-source **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** on CPU **or** **[Sarvam.ai](https://www.sarvam.ai/)** (hosted) — chosen per profile, or per-upload | Whisper = zero-key/on-prem; Sarvam = Hindi-native (no Whisper-to-Urdu drift), 10 Indic langs |
| **LLM**      | Any OpenAI-compatible `/v1` endpoint — **native [Ollama](https://ollama.com/) on the host by default**; [Groq](https://groq.com/), vLLM, or OpenAI all work | One client, swap provider with `LLM_BASE_URL` / `LLM_MODEL` — no code change |
| **Embeddings** | **Local `intfloat/multilingual-e5-large` on CPU by default**, or hosted **[Cohere](https://cohere.com/) `embed-multilingual-v3.0`** | Both 1024-d → identical pgvector schema; switch with `EMBEDDING_PROVIDER` |
| **Vector DB**  | Postgres 16 + `pgvector`           | One database, ivfflat → HNSW on scale-up                 |
| **Queue**    | Celery + Redis                      | At-least-once with `task_acks_late=True`                 |
| **Storage**  | MinIO (S3-compatible)               | Raw audio, transcript JSON, pipeline artifacts           |
| **UI**       | Next.js 14 (App Router) + Cytoscape.js + Plotly | Cluster explorer, memory graph, retrieval playground, upload   |

The default stack is **on-prem and zero-key**: a native Ollama LLM on the host
and local e5 embeddings on CPU — **no paid API and no GPU required**. Both
shipped profiles use this LLM + embedding setup; they differ only in STT
(open-source Whisper vs. hosted Sarvam). Set `EMBEDDING_PROVIDER=cohere` or point
`LLM_BASE_URL` at Groq to move those layers to a hosted API instead.

Audio → STT (Whisper natively, or Sarvam chunked on silences <30 s each and
transcribed in parallel) → heuristic speaker assignment → LLM JSON-mode
extraction → embedding (local e5 or Cohere) → HDBSCAN + incremental assignment →
LLM canonicalization + memory edges. Pre-labeled transcript JSON can be ingested
directly, skipping the STT stage.

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
ollama pull gemma4:latest      # model used by .env.opensource (LLM_MODEL)

# Pick a profile:
cp .env.opensource .env       # fully open source (Whisper STT) — uses gemma4:latest
# cp .env.sarvam .env         # Sarvam STT (uses qwen2.5:7b-instruct) — set SARVAM_API_KEY

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
ollama pull gemma4:latest          # for .env.opensource
# ollama pull qwen2.5:7b-instruct  # for .env.sarvam
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

Ingest an **audio** call (the configured STT provider transcribes it):

```bash
curl -X POST http://localhost:8080/ingest \
  -H 'Content-Type: application/json' \
  -d '{
        "source_uri": "minio://audio-raw/call_2026_05_27_001.wav",
        "is_transcript": false,
        "metadata": {"campaign": "renewal_q1", "channel": "outbound"}
      }'
```

**Upload a local file** straight from disk (multipart — this is what the
dashboard's upload box on the Calls page calls). Audio goes to the `audio-raw`
bucket and is transcribed; a `.json` file is treated as a pre-labeled
transcript. Optionally pick the STT provider per upload:

```bash
curl -X POST http://localhost:8080/ingest/upload \
  -F 'file=@call_2026_05_27_001.wav' \
  -F 'campaign=renewal_q1' \
  -F 'stt_provider=whisper'        # or sarvam; omit to use the .env default
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
| `worker-cpu`         | python:3.11 (fast lane: extract/embed/cluster + Sarvam STT) | no  | —          |
| `worker-stt`         | python:3.11 (heavy lane: local Whisper STT, queue `stt.heavy`)| no  | —          |
| `worker-gpu`         | nvidia/cuda + python:3.11      | opt | —          |
| `postgres`           | pgvector/pgvector:pg16         | no  | 5432       |
| `redis`              | redis:7-alpine                 | no  | 6379       |
| `minio`              | minio/minio                    | no  | 9000, 9001 |
| `flower`             | mher/flower                    | no  | 5555       |
| `frontend`           | node:20 (Next.js 14 standalone)| no  | 3001 → 3000|
| `beat`               | python:3.11                    | no  | —          |

Slow CPU-bound local Whisper transcription is isolated on `worker-stt` (queue
`stt.heavy`) so it can't starve fast Sarvam transcription or the light
downstream stages running on `worker-cpu`. The default fleet is **entirely
CPU** (local e5 embeddings run on `worker-cpu`); the CUDA `worker-gpu` is opt-in
(`--profile gpu`) and only takes the embedding queue on NVIDIA/Linux hosts.

By default **no provider is a container** that you manage: the LLM is a native
Ollama on the host (reached via `host.docker.internal`), embeddings run locally
inside `worker-cpu`, and STT is local Whisper. Switching `EMBEDDING_PROVIDER=cohere`
or pointing `LLM_BASE_URL`/Sarvam at a hosted API moves those layers out to
public HTTPS endpoints (then only the relevant API key is needed).

---

## GPU sizing (on-prem / hosted-free deployments only)

The default stack runs **CPU-only** — the LLM offloads to a native Ollama on
the host (Metal on Mac) and e5 embeddings run on CPU. A GPU is purely optional
speed-up. The numbers below apply if you put the LLM and `EMBEDDING_PROVIDER=local`
on a single RTX 4080 (16 GB):

| Workload                          | VRAM          | Notes                              |
|-----------------------------------|---------------|------------------------------------|
| Ollama LLM (e.g. `qwen2.5:7b-instruct` q4_K_M) | ~5–6 GB | If you run Ollama on the GPU      |
| multilingual-e5-large             | ~2 GB         | When `EMBEDDING_PROVIDER=local` on `worker-gpu`; loaded once per worker |
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

# LLM + embeddings run on-prem by default: install Ollama
# (https://ollama.com/download), `ollama serve &`, `ollama pull gemma4:latest`,
# and point LLM_BASE_URL at http://localhost:11434/v1 (already the default).
# Embeddings download intfloat/multilingual-e5-large to disk on first use.
# To use hosted providers instead, set EMBEDDING_PROVIDER=cohere / point
# LLM_BASE_URL at Groq and supply the keys in .env.

# Then run the app processes (one worker covering every queue is fine for dev):
uvicorn app.api.main:app --reload --port 8080
celery -A app.workers.celery_app worker --loglevel=INFO -Q stt.heavy,celery,gpu.heavy,default --concurrency=2
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
  and rebuild the workers, switch to the open-source profile (`STT_PROVIDER=whisper`),
  or use `STT_PROVIDER=none` and ingest pre-labeled transcripts only.
- **Sarvam returns `429 Too Many Requests` on long calls** — long audio is split
  into many <30 s chunks; transcribing them in parallel can exceed Sarvam's rate
  limit. Use the open-source Whisper profile for long local test calls (no API
  limits), or lower concurrency / spread the load.
- **First Whisper or e5 run sits in `*_running` for minutes** — the first call
  downloads model weights (Whisper `large-v3` ~3 GB, e5-large ~2 GB) into the
  shared `hf_cache` volume. Subsequent runs reuse the cache. Watch
  `docker logs v2t-worker-stt` (Whisper) or `v2t-worker-cpu` (embeddings).
- **`ollama pull` hangs** — Ollama runs **natively on the host** (not a container);
  first pull is several GB. Watch the `ollama serve` terminal for progress.
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
    stt/           Sarvam + faster-whisper clients + transcript loader + speaker heuristic
    llm/           OpenAI-compatible LLM client (Ollama / Groq / vLLM / OpenAI)
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
