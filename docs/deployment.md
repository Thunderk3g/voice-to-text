# Deployment

This document covers production-grade deployment notes for v2t. The local
stack in `docker-compose.yml` is fine for demos and tens of thousands of
calls; everything below is what you reach for once the system goes
multi-tenant or grows past ~1 lakh embeddings.

> **Reminder:** STT and speaker diarization are handled UPSTREAM by Servo AI.
> Nothing in this document re-introduces them.

---

## 1. Lift docker-compose to a managed environment

| Local            | Managed equivalent                                       |
|------------------|-----------------------------------------------------------|
| `postgres`       | AWS RDS for PostgreSQL with the `pgvector` extension (≥ pg15). Enable IAM auth, set `maintenance_work_mem >= 1GB` before building the vector index. |
| `redis`          | AWS ElastiCache for Redis 7, cluster mode disabled (Celery does not need it). Keep TLS on, use AUTH tokens. |
| `minio`          | S3. `app/services/audio/io.py` already supports `s3://bucket/key` URIs — bind the worker pods to an IRSA / workload-identity role and remove the MinIO credentials from the env. |
| `ollama`         | Either keep an Ollama Deployment on a GPU node group, or front it with a dedicated inference service. See §2. |
| `worker-cpu`     | Horizontally scaled K8s Deployment, 4–8 vCPU per replica. |
| `worker-gpu`     | DaemonSet pinned to GPU nodes, one replica per GPU.       |
| `beat`           | Single replica only — Celery beat is not HA-safe. Use a leader-election sidecar or a managed cron. |
| `flower`         | Optional; restrict access via an authenticating proxy.    |

Switching transcripts from MinIO to S3 is a no-op at the code level: the
ingest payload sends `source_uri: "s3://transcripts-prod/<key>"` and
`download_to_temp` in `app/services/audio/io.py` resolves it.

---

## 2. Scaling

### worker-cpu (extraction / clustering / canonicalization / edges)

- Stateless. Scale on CPU utilization or Celery queue depth.
- Embedding is **not** done here — it's queued onto `gpu.heavy`.
- The HTTP fan-out to Ollama is async; expect a single `worker-cpu` replica
  to keep one Ollama replica saturated at moderate temperatures.

### worker-gpu (embeddings only)

- Concurrency=1 per replica. multilingual-e5-large is ~2 GB on device and
  benefits from sequential batches rather than parallel sessions.
- For multi-GPU hosts, run one worker per GPU and pin via
  `CUDA_VISIBLE_DEVICES`. Celery routing key `gpu.heavy` already isolates
  the queue.
- Partition the queue per tenant when you outgrow one cluster: add Celery
  routes `gpu.heavy.tenant_a`, `gpu.heavy.tenant_b`, etc. and dedicate
  worker replicas to each.

### Ollama

- Ollama does not share GPU memory across replicas. Each replica holds the
  full Qwen2.5-7B weights. To scale: run N independent Ollama replicas
  behind round-robin DNS or an L4 load balancer (sticky sessions are not
  required because every chat call is stateless on our side).
- Set `OLLAMA_NUM_PARALLEL=<n>` to allow a single replica to process
  multiple chat completions in parallel within its GPU budget.
- Keep `OLLAMA_KEEP_ALIVE=24h` (already in compose) so the model stays
  resident.

---

## 3. pgvector: ivfflat → HNSW

The default index is `ivfflat`, which is great up to ~1 lakh vectors. Past
that, HNSW gives roughly an order-of-magnitude better recall/latency tradeoff
at the cost of a longer index build.

Drop the old index and create the new one:

```sql
DROP INDEX IF EXISTS embeddings_vector_ivfflat;

-- HNSW. m=16 / ef_construction=64 is a balanced default for 1024-dim e5
-- embeddings; raise ef_construction to 128 if you have build-time headroom.
CREATE INDEX embeddings_vector_hnsw
  ON embeddings
  USING hnsw (vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- At query time tune recall vs latency per session:
SET hnsw.ef_search = 100;
```

Build the index inside a maintenance window — HNSW is fully blocking. Bump
`maintenance_work_mem` to 4–8 GB on the session that builds it.

---

## 4. Secret management

- Never bake credentials into the container images. The Dockerfiles in this
  repo only copy code; secrets come in at runtime.
- Local: `.env` file (already gitignored).
- Production: Docker secrets, Kubernetes `Secret` mounted as env vars, or a
  cloud secret manager (AWS Secrets Manager / GCP Secret Manager / Vault)
  resolved via init-container or workload-identity-aware sidecar.
- `app/core/config.py` reads from environment variables via pydantic-settings;
  any secret manager that materializes envs at process start is compatible.
- Rotate `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` (or their S3 equivalents)
  when staff turn over. `LLM_API_KEY` should be set to your provider's API
  key (Groq, OpenAI, etc.). For local Ollama deployments, this field is a
  placeholder — Ollama itself does not check authentication. If you front it
  with an auth proxy, plumb the real token through this var.

---

## 5. OpenTelemetry collector

The optional `otel-collector` service in `docker-compose.yml` is commented
out. Drop this file at `infra/otel/otel-config.yaml` and uncomment the block
to enable tracing:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1024

exporters:
  otlphttp:
    endpoint: ${OTLP_DEST_ENDPOINT}    # e.g. https://otlp.your-vendor.example
    headers:
      authorization: Bearer ${OTLP_DEST_TOKEN}
  prometheus:
    endpoint: 0.0.0.0:9464

service:
  pipelines:
    traces:
      receivers:  [otlp]
      processors: [batch]
      exporters:  [otlphttp]
    metrics:
      receivers:  [otlp]
      processors: [batch]
      exporters:  [prometheus]
```

Then point services at it by setting `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`.

---

## 6. Embedding cache warming

The embedding cache is Redis-backed (see `app/services/embedding/cache.py`)
and keyed by `sha1(prefixed_text):{model}`. When you upgrade the embedding
model — or when you swap from CPU to GPU and back — the cache **does not
invalidate**, because the model name is part of the key. After upgrading,
warm the cache for known canonical FAQs so the first wave of user queries
hits warm vectors:

```python
from app.services.embedding.e5 import EmbeddingService
from app.services.embedding.cache import EmbeddingCache

cache = EmbeddingCache.from_settings()
svc = EmbeddingService(cache=cache)
await svc.embed(known_faq_texts, role="passage")
```

For the daily recluster (`v2t.batch_recluster`) the cache wins back roughly
60–80% of embedding cost on a steady-state corpus.

---

## 7. Backup & restore

### Postgres

- `pg_dump --format=custom -d v2t > v2t-$(date +%F).dump` nightly to S3.
- Test restore quarterly into a scratch RDS instance: `pg_restore -d v2t_scratch -j 4 v2t-YYYY-MM-DD.dump`.
- Keep WAL archiving on (`wal_level=replica`, `archive_mode=on`) so PITR
  works for the last 7 days.

### Object storage

- Enable cross-region replication on the `transcripts` and
  `pipeline-artifacts` buckets. Lifecycle policy: keep transcripts forever,
  expire artifacts after 90 days.
- MinIO: `mc mirror --watch local/transcripts dr/transcripts`.

---

## 8. Monitoring

`api` exposes Prometheus metrics at `GET /metrics`. Scrape it:

```yaml
- job_name: v2t-api
  metrics_path: /metrics
  static_configs:
    - targets: ['api:8080']

- job_name: v2t-worker-cpu
  metrics_path: /metrics
  static_configs:
    - targets: ['worker-cpu:9100']

- job_name: v2t-worker-gpu
  metrics_path: /metrics
  static_configs:
    - targets: ['worker-gpu:9100']
```

### Suggested SLOs

| Metric                                 | SLO                  |
|----------------------------------------|----------------------|
| `POST /ingest` p95 latency             | < 250 ms             |
| `POST /search` p95 latency             | < 400 ms (cold), < 120 ms (warm cache) |
| `v2t_extraction_processed_total` rate  | > 95% of `calls_ingested_total` over 1h |
| `v2t_stage_duration_seconds{stage="embed"}` p95 | < 4 s per call |
| `v2t_stage_duration_seconds{stage="cluster"}` p95 | < 6 s per call |
| Ollama 5xx rate                        | < 0.5%               |
| Celery queue depth on `gpu.heavy`      | < 50 (alert at 200)  |
