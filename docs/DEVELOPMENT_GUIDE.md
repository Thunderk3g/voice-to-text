# v2t — Development Guide

> Insurance call intelligence platform: ingest calls, extract structured questions, cluster them, surface FAQs and a memory graph.
> Built for Bajaj Life. Runs entirely on local Docker.

---

## 1. What this project is

**v2t** turns raw insurance customer calls (or pre-labeled transcripts) into:

- structured **extracted questions** with intent, language, and speaker metadata
- **semantic clusters** of recurring customer concerns
- a **canonical FAQ** distilled from each cluster
- a **memory graph** of how concerns relate to each other
- a **Next.js dashboard** that surfaces all of the above

The transcription path uses **Sarvam.ai** (Hindi + 10 Indic languages). The LLM extraction path goes to **Groq** (`openai/gpt-oss-120b` + `whisper-large-v3`) over an OpenAI-compatible HTTPS API. Embeddings use `intfloat/multilingual-e5-large`. Vector search runs on **Postgres + pgvector**.

---

## 2. Architecture

```
                 +------------------+
                 |  Next.js (3001)  |
                 |   dashboard      |
                 +---------+--------+
                           | /api/v2t/* (rewrite)
                           v
+------------------+   +---+--------------+   +-------------------+
|  Browser / user  |-> | FastAPI (8080)   |-> |  Groq HTTPS API   |
+------------------+   |  app.api.main    |   |  api.groq.com/v1  |
                       +---+----+----+----+   +-------------------+
                           |    |    |
            +--------------+    |    +-------------------+
            v                   v                        v
   +-----------------+   +-------------+         +----------------+
   | Postgres + PGV  |   |   Redis     |         |     MinIO      |
   | (compliance_db) |   |  broker/result        |  transcripts / |
   | pgvector 0.8.2  |   |             |         |  pipeline      |
   +-----------------+   +------+------+         |  artifacts     |
                                |                +----------------+
                                v
                  +-----------------------------+
                  |  Celery worker (CPU queue)  |
                  |  ingest / extract / cluster |
                  |  canonicalize / memory      |
                  +-----------------------------+
                                |
                                v
                  +-----------------------------+
                  |  Celery beat (scheduler)    |
                  |  daily recluster, etc.      |
                  +-----------------------------+

                  +-----------------------------+
                  |  Flower (5555) — observe    |
                  +-----------------------------+

   Skipped (no NVIDIA runtime on this host):
   - worker-gpu (embedding queue) — embeddings stall today
   - ollama / ollama-init        — Groq replaces this
```

### Data flow

1. `POST /ingest` accepts an audio URL **or** a pre-labeled transcript JSON.
2. Audio path: chunked on silence (Sarvam 30s limit) -> Sarvam transcription -> speaker heuristic -> stitched transcript.
3. Transcript -> LLM extraction (Groq) -> `ExtractedQuestionORM` rows.
4. Each question -> e5-large embedding -> `pgvector` row.
5. Periodic recluster (HDBSCAN) -> `SemanticCluster` rows.
6. Cluster -> canonical FAQ + memory-edge inference.
7. Dashboard reads via FastAPI routes.

---

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI 0.115 | uvicorn, OpenTelemetry, Prometheus `/metrics` |
| Workers | Celery 5.4 + Redis 5.2 | CPU queue `celery`, GPU queue `gpu.heavy` |
| DB | Postgres 16 + pgvector 0.8.2 + pgcrypto | Alembic at `0001_initial` |
| Object store | MinIO | buckets: `transcripts`, `pipeline-artifacts` |
| LLM | Groq (`openai/gpt-oss-120b`) | OpenAI-compatible `/v1` |
| STT | Sarvam.ai `saarika:v2.5` | Hindi + Indic |
| Embeddings | `intfloat/multilingual-e5-large` (1024-dim) | runs on GPU worker (today: down) |
| Clustering | HDBSCAN + UMAP | `min_cluster_size=8` |
| Frontend | Next.js 14.2 (standalone) | proxies `/api/v2t/*` -> `http://api:8080/*` |
| Auth (frontend) | Firebase (Vite vars in `.env`) | not yet enforced server-side |
| Tracing | LangSmith + OTLP HTTP | LangSmith key in `.env` |
| TLS | Corporate root bundle | see Section 6 |

---

## 4. Repository structure

```
voice-to-text/
  app/                              FastAPI + Celery code
    api/
      main.py                       app factory, lifespan, middleware
      routes/                       analytics, calls, clusters, faq, feedback,
                                    health, ingest, memory_graph, search
    clustering/                     HDBSCAN + UMAP engine
    core/                           settings, logging, tracing
    db/
      models.py                     SQLAlchemy ORM (Call, Utterance,
                                    ExtractedQuestionORM, Embedding,
                                    SemanticCluster, ClusterMemberORM,
                                    CanonicalFAQORM, MemoryEdgeORM,
                                    FeedbackAnnotationORM)
      repositories.py
      session.py
    memory/                         memory-graph inference
    models/schemas.py               Pydantic v2 request/response models
    pipelines/                      orchestration glue
    prompts/                        LLM prompt templates
    scripts/                        seed_data etc.
    services/
      embedding/e5.py               multilingual-e5-large
      extraction/llm_extractor.py   Groq-compatible client
      llm/
      stt/                          Sarvam adapter + speaker heuristic
    tests/                          unit + integration
    workers/
      celery_app.py
      tasks.py                      task definitions
  frontend/                         Next.js 14 dashboard
    components/                     CytoscapeGraph, charts, tables
    pages|app/                      routes
    next.config.js                  rewrites /api/v2t/* -> ${NEXT_PUBLIC_API_BASE_URL}
    types/modules.d.ts              ambient module declarations
    Dockerfile                      node:20-slim builder + runner
  infra/
    certs/                          corporate root CAs (PEM)
    postgres/Dockerfile             pgvector + pgcrypto image
  scripts/
    install-corp-ca.ps1             extract corporate roots from Windows store
  Dockerfile                        API image (python:3.11-slim)
  Dockerfile.worker-cpu             CPU worker image
  Dockerfile.worker-gpu             CUDA worker image (currently unused)
  Dockerfile.beat                   beat scheduler image
  docker-compose.yml                full local stack
  requirements.txt                  Python pins
  alembic.ini                       migration config
  .env / .env.example               environment variables
  data/                             sample audio + parquet exports
  docs/                             this guide, contracts, deployment, gpu sizing
```

---

## 5. Environment

A working `.env` is required at repo root. The shape matches `.env.example` with these notable values for this deployment:

- `POSTGRES_USER=compliance_user`, `POSTGRES_DB=compliance_db` (Bajaj naming)
- `LLM_BASE_URL=https://api.groq.com/openai/v1/`
- `LLM_MODEL=openai/gpt-oss-120b`, `LLM_TRANSCRIPTION_MODEL=whisper-large-v3`
- `LLM_INSECURE_TLS=true` (dev only — bypasses some TLS checks in code; the corporate CA bundle is the real fix)
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080` (used by browser-side fetches)
- `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT=compliance-project`
- Firebase `VITE_FIREBASE_*` vars for the dashboard

### Required secrets

| Variable | Source |
|---|---|
| `SARVAM_API_KEY` | Sarvam dashboard |
| `LLM_API_KEY` | Groq console |
| `LANGSMITH_API_KEY` | smith.langchain.com |
| `VITE_FIREBASE_*` | Firebase console |

---

## 6. Corporate TLS bundle (Cisco Umbrella + BajajLife root)

Outbound HTTPS from inside containers (PyPI, npm, Groq, etc.) goes through the corporate Cisco Umbrella MITM proxy and the BajajLife root CA. Without trusting both roots, **everything that uses HTTPS inside a container fails the TLS handshake**.

This was the dominant source of build/runtime failures.

### How it works

1. `scripts/install-corp-ca.ps1` extracts both roots from the Windows certificate store and writes a concatenated PEM bundle to `infra/certs/bajaj-root.pem` (and a copy under `frontend/certs/`).
2. Each Dockerfile copies the PEM bundle into `/usr/local/share/ca-certificates/` and runs `update-ca-certificates`, then sets `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `CURL_CA_BUNDLE`, `PIP_CERT`, and `NODE_EXTRA_CA_CERTS` so pip / httpx / requests / curl / Node all use it.
3. The frontend uses `node:20-slim` (Debian) instead of `node:20-alpine` because the proxy corrupts the binary `.apk` payloads from `dl-cdn.alpinelinux.org` even after TLS is trusted.

### Verification

From inside the API container:

```bash
docker exec v2t-api curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer $LLM_API_KEY" \
  https://api.groq.com/openai/v1/models
# expected: HTTP 200
```

### If new corporate CAs ship

Re-run `scripts/install-corp-ca.ps1` to refresh the bundle, then rebuild affected images:

```powershell
.\scripts\install-corp-ca.ps1
docker compose build api worker-cpu beat frontend
docker compose up -d --force-recreate api worker-cpu beat frontend
```

---

## 7. Current service status

All long-running services are healthy. `minio-setup` is a one-shot init job — its exit(0) is correct.

| Service | Container | Host port | State | Purpose |
|---|---|---|---|---|
| FastAPI | `v2t-api` | 8080 | healthy | REST API, OpenAPI at `/docs` |
| CPU worker | `v2t-worker-cpu` | (internal) | healthy | ingest, extract, cluster, canonicalize, memory edges |
| Beat | `v2t-beat` | (internal) | healthy | periodic scheduler |
| Flower | `v2t-flower` | 5555 | healthy | Celery monitoring UI |
| Frontend | `v2t-frontend` | 3001 | healthy | Next.js dashboard |
| Postgres | `v2t-postgres` | 5432 | healthy | `compliance_db` + pgvector |
| Redis | `v2t-redis` | 6379 | healthy | Celery broker + result + cache |
| MinIO | `v2t-minio` | 9000 / 9001 | healthy | object storage + console |
| MinIO setup | `v2t-minio-setup` | (one-shot) | Exited (0) | created buckets, then exited |

### Open URLs

| URL | Notes |
|---|---|
| `http://localhost:3001/` | Frontend dashboard |
| `http://localhost:8080/docs` | OpenAPI |
| `http://localhost:8080/healthz` | Shallow health |
| `http://localhost:8080/readyz` | Deep health (DB + Redis) |
| `http://localhost:5555/` | Flower |
| `http://localhost:9001/` | MinIO console (`minio_admin` / `minio_admin_pw`) |

### Exposed API routes

```
GET    /healthz
GET    /readyz
POST   /ingest
GET    /calls/{call_id}
GET    /calls/{call_id}/questions
GET    /calls/{call_id}/utterances
POST   /search
GET    /cluster/{cluster_id}
GET    /faq
GET    /memory-graph
GET    /analytics
POST   /feedback
```

---

## 8. Operating the stack

### Start (non-GPU subset)

```bash
docker compose up -d \
  postgres redis minio minio-setup \
  api worker-cpu beat flower frontend
```

### Stop

```bash
docker compose down
```

Add `-v` to also drop the `postgres_data`, `minio_data`, `hf_cache` volumes (destructive — wipes DB and object storage).

### Logs

```bash
docker compose logs -f api
docker compose logs -f worker-cpu
docker logs v2t-beat --tail 50
```

### Rebuild after code changes

For Python route changes during dev, you can `docker cp` the file in and restart the container:

```bash
docker cp ./app/api/routes/analytics.py v2t-api:/app/app/api/routes/analytics.py
docker restart v2t-api
```

For real builds:

```bash
docker compose build api worker-cpu beat
docker compose up -d --force-recreate api worker-cpu beat
```

---

## 9. Development log

What changed since the initial commit (`252386d Initial v2t platform`), why, and where.

### 9.1 `.env` created

`.env.example` was committed but `.env` was missing, so every compose command warned about undefined variables. Created `.env` from the template, then layered Bajaj-specific config: `compliance_user/compliance_db` for Postgres, Groq for the LLM, LangSmith/Firebase config, and an `OLLAMA_MODEL` placeholder so the legacy `ollama-init` service's interpolation stops warning even though Ollama isn't started.

### 9.2 Corporate TLS bundle wired into every image

- Built `infra/certs/bajaj-root.pem` from Windows root store (Cisco Umbrella + BajajLife-Root-CA).
- Added a `COPY infra/certs/...` + `update-ca-certificates` block to `Dockerfile`, `Dockerfile.worker-cpu`, `Dockerfile.beat`, `Dockerfile.worker-gpu`. Pip / httpx / requests / curl now find the cert via `REQUESTS_CA_BUNDLE` and friends.
- Switched `frontend/Dockerfile` from `node:20-alpine` to `node:20-slim`. The MITM proxy corrupts the body of Alpine package downloads even when TLS is fixed (signature check fails on libucontext / gcompat). Debian's `deb.debian.org` mirror uses plain HTTP and passes through cleanly.

### 9.3 Build dependency pins

- `requirements.txt`: `indic-transliteration==2.3.66` did not exist on PyPI; bumped to `2.3.67`.
- Frontend (`tsconfig.json` already includes ambient declarations) — added `frontend/types/modules.d.ts` with `declare module "react-cytoscapejs"`.
- `frontend/components/CytoscapeGraph.tsx`: `cytoscape.Stylesheet[]` was renamed in newer `@types/cytoscape`. Loosened the typing rather than rewriting every CSS rule.
- Frontend runner stage `mkdir -p public` because the repo has no `frontend/public` directory and Next.js standalone output expects it.

### 9.4 FastAPI middleware ordering

`app/api/main.py` was calling `FastAPIInstrumentor.instrument_app(app)` inside the `_lifespan` async context manager. Recent Starlette refuses to add middleware after the application starts:

```
RuntimeError: Cannot add middleware after an application has started
```

Moved OTel + logging init into `create_app()` before any middleware is added.

### 9.5 Healthchecks

- `Dockerfile.beat` added `procps` so the `pgrep -f 'celery.*beat'` healthcheck works.
- `docker-compose.yml` replaced the frontend's `wget` healthcheck with a Node-native `http.get` check (`node:20-slim` ships no wget).

### 9.6 Port remap

Frontend remapped from host port 3000 to **3001** because another `node.exe` on the host was already bound to 3000. Container internal port is still 3000.

### 9.7 Frontend -> API proxy

Two bugs:

1. `frontend/next.config.js` defaulted to `http://api:8000` (wrong port — API listens on 8080).
2. Rewrites are baked at **build** time, so the `environment: NEXT_PUBLIC_API_BASE_URL` block in compose never reached the build.

Fixes:

- Corrected the fallback to `http://api:8080`.
- Frontend Dockerfile now declares `ARG NEXT_PUBLIC_API_BASE_URL=http://api:8080` and exports it as `ENV` before `npm run build`, so overrides via `docker compose build` flow through correctly.

### 9.8 Six API routes imported model classes under the wrong names

The ORM module exports `ExtractedQuestionORM`, `ClusterMemberORM`, `CanonicalFAQORM`, `MemoryEdgeORM`, `FeedbackAnnotationORM`, but six route files imported them under the non-ORM names (`ExtractedQuestion`, `ClusterMember`, ...) and renamed them with `as`. Every affected route raised `ImportError` at first request.

Fixed: `analytics.py`, `calls.py`, `search.py`, `feedback.py`, `faq.py`, `memory_graph.py`.

Smoke-test result after the fix:

| Route | Status | Body |
|---|---|---|
| `GET /analytics` | 200 | zeroed metrics object |
| `GET /faq` | 200 | `[]` |
| `GET /memory-graph` | 200 | `{nodes: [], edges: []}` |

---

## 10. What is not running and why

| Service | Skipped because | Impact |
|---|---|---|
| `worker-gpu` | Docker Desktop on this host has no NVIDIA runtime | None for embeddings — the dedicated `worker-embed` service consumes the `gpu.heavy` queue on CPU (see below). `worker-gpu` is now an opt-in GPU alternative, behind the `gpu` compose profile. |
| `ollama` / `ollama-init` | `.env` points the LLM at Groq, not Ollama | None. Already removed from `docker-compose.yml`; only a stale header comment remains. |

### Embeddings on CPU (resolved)

The `gpu.heavy` (embedding) queue is consumed by the **`worker-embed`** service:
it reuses the `Dockerfile.worker-cpu` image (which already pins `torch==2.4.1` +
`sentence-transformers==3.3.1`), runs `-Q gpu.heavy --concurrency=1`, and pins
`EMBEDDING_DEVICE=cpu`. `.env` also defaults `EMBEDDING_DEVICE=cpu`. So
`ingest -> extract -> embed -> cluster -> FAQ` runs end-to-end with no GPU.

To embed on a GPU instead: `docker compose --profile gpu up` (brings up
`worker-gpu`, which pins `EMBEDDING_DEVICE=cuda`) and optionally
`docker compose up -d --scale worker-embed=0` so the two don't both consume the
queue. Previously this section recommended building a CPU worker from scratch —
that's now done as `worker-embed` in compose.

---

## 11. Roadmap

Not committed work — proposals only. Pick a direction before starting.

### Near term (this week)
- Push a synthetic transcript through `/ingest` end-to-end; verify Groq extraction returns `ExtractedQuestionORM` rows.
- CPU embedding worker (Section 10) so the rest of the pipeline can be exercised.
- Capture the Sarvam path with a real audio sample once `SARVAM_API_KEY` is in place.
- Frontend auth: actually wire `VITE_FIREBASE_*` to gate the dashboard.

### Mid term
- Server-side auth on the FastAPI side (Firebase ID-token verifier middleware).
- Backfill drift / cluster-growth charts in `/analytics`.
- LangSmith trace links from individual `/ingest` calls into the dashboard.

### Longer term
- Move Sarvam STT to a Celery task so `/ingest` returns immediately.
- Multi-tenant data isolation (per insurance product / region).
- Production deployment story (Kubernetes manifests for the same services).

---

## 12. Files added or modified since `252386d`

| Path | Change |
|---|---|
| `.env` | created |
| `infra/certs/bajaj-root.pem` | created (corporate roots) |
| `infra/certs/.gitkeep`, `infra/certs/README.md` | created |
| `frontend/certs/.gitkeep`, `frontend/certs/README.md` | created |
| `frontend/types/modules.d.ts` | created |
| `scripts/install-corp-ca.ps1` | created |
| `Dockerfile` | cert install + env vars |
| `Dockerfile.worker-cpu` | cert install + env vars |
| `Dockerfile.beat` | cert install + procps + env vars |
| `Dockerfile.worker-gpu` | cert install + env vars |
| `frontend/Dockerfile` | switched to `node:20-slim`, cert install, build-arg API URL |
| `frontend/next.config.js` | default URL `:8000` -> `:8080` |
| `frontend/components/CytoscapeGraph.tsx` | relaxed `Stylesheet` typing |
| `docker-compose.yml` | frontend port 3001, healthcheck, build context tweaks |
| `requirements.txt` | `indic-transliteration` 2.3.66 -> 2.3.67 |
| `app/api/main.py` | OTel + logging init moved out of lifespan |
| `app/api/routes/analytics.py` | model import fix |
| `app/api/routes/calls.py` | model import fix |
| `app/api/routes/search.py` | model import fix |
| `app/api/routes/feedback.py` | model import fix |
| `app/api/routes/faq.py` | model import fix |
| `app/api/routes/memory_graph.py` | model import fix |
| `app/core/config.py` | cleanup |
| `app/services/llm/ollama_client.py` | cleanup |
| `docs/contracts.md`, `docs/deployment.md`, `docs/gpu_sizing.md` | doc tweaks |

---

## 13. Quick reference

```bash
# Bring the stack up
docker compose up -d postgres redis minio minio-setup api worker-cpu beat flower frontend

# Status
docker ps --filter name=v2t- --format 'table {{.Names}}\t{{.Status}}'

# Smoke tests
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/analytics
curl http://localhost:3001/api/v2t/analytics

# Groq reachability (from inside container, exercises the corporate cert)
docker exec v2t-api curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer $LLM_API_KEY" \
  https://api.groq.com/openai/v1/models

# Postgres shell
docker exec -it v2t-postgres psql -U compliance_user -d compliance_db

# Tail worker logs
docker compose logs -f worker-cpu
```
