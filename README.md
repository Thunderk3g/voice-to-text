# v2t — Voice-to-Text Insurance Call Intelligence

> Multilingual insurance call intelligence platform.
> Audio in → searchable semantic memory + FAQ clusters + memory graph out.

[![Repo](https://img.shields.io/badge/repo-Thunderk3g%2Fvoice--to--text-181717?logo=github)](https://github.com/Thunderk3g/voice-to-text)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000?logo=next.js)
![Postgres](https://img.shields.io/badge/Postgres-16%2Bpgvector-336791?logo=postgresql&logoColor=white)
![Ollama](https://img.shields.io/badge/LLM-Ollama%20qwen2.5--7b-7B4DBA)
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
| **LLM**      | [Ollama](https://ollama.com/) + `qwen2.5:7b-instruct` | Local inference, OpenAI-compatible /v1                   |
| **Embeddings** | `intfloat/multilingual-e5-large`  | Cross-lingual aligned 1024-d space                       |
| **Vector DB**  | Postgres 16 + `pgvector`           | One database, ivfflat → HNSW on scale-up                 |
| **Queue**    | Celery + Redis                      | At-least-once with `task_acks_late=True`                 |
| **Storage**  | MinIO (S3-compatible)               | Raw audio, transcript JSON, pipeline artifacts           |
| **UI**       | Next.js 14 (App Router) + Cytoscape.js + Plotly | Cluster explorer, memory graph, retrieval playground   |

Audio → Sarvam (chunked on silences, <30 s each, transcribed in parallel) →
heuristic speaker assignment → Ollama JSON-mode extraction → e5 embedding →
HDBSCAN + incremental assignment → Ollama canonicalization + memory edges.

See [`docs/architecture.md`](docs/architecture.md) for the full diagram.

---

## Quickstart (Linux / WSL2 with NVIDIA Container Toolkit)

```bash
git clone https://github.com/Thunderk3g/voice-to-text.git
cd voice-to-text
cp .env.example .env
# fill in SARVAM_API_KEY (and any other secrets) in .env

docker compose up -d --build
docker compose run --rm ollama-init          # pulls qwen2.5:7b-instruct (~4–5 GB, one time)

# Seed the 6 sample multilingual transcripts:
python -m app.scripts.seed_data --api-url http://localhost:8080
```

Open:

| URL                          | What                                             |
|------------------------------|--------------------------------------------------|
| http://localhost:3000        | Next.js dashboard (cluster explorer, graph, …)   |
| http://localhost:8080/docs   | FastAPI Swagger                                  |
| http://localhost:8080/metrics| Prometheus exposition                            |
| http://localhost:5555        | Flower (Celery queue monitor)                    |
| http://localhost:9001        | MinIO console                                    |

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
| `worker-gpu`         | nvidia/cuda + python:3.11      | yes | —          |
| `ollama`             | ollama/ollama                  | yes | 11434      |
| `postgres`           | pgvector/pgvector:pg16         | no  | 5432       |
| `redis`              | redis:7-alpine                 | no  | 6379       |
| `minio`              | minio/minio                    | no  | 9000, 9001 |
| `flower`             | mher/flower                    | no  | 5555       |
| `frontend`           | node:20 (Next.js 14 standalone)| no  | 3000       |
| `beat`               | python:3.11                    | no  | —          |

Sarvam.ai is **not** a container — calls go out over the public Sarvam HTTPS
API. Only an API key is needed.

---

## GPU sizing — RTX 4080 (16 GB)

| Workload                          | VRAM          | Notes                              |
|-----------------------------------|---------------|------------------------------------|
| Ollama `qwen2.5:7b-instruct` q4_K_M | ~5–6 GB     | Default Ollama quantisation        |
| multilingual-e5-large             | ~2 GB         | Loaded once per worker-gpu process |
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

# Ollama: install natively + pre-pull the model.
#   https://ollama.com/download
ollama serve &
ollama pull qwen2.5:7b-instruct

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

- [ ] Switch pgvector index from ivfflat to HNSW once corpus passes ~1 L vectors
- [ ] Wire Sarvam **Batch STT API** (returns proper diarization) to replace the
      on-board speaker heuristic
- [ ] Pluggable LLM backend (vLLM / OpenAI / Anthropic / Bedrock) behind the same
      OpenAI-compatible interface
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
    llm/           Ollama OpenAI-compatible client
    extraction/    LLM-driven question extractor
    embedding/     multilingual-e5-large service + Redis cache
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
