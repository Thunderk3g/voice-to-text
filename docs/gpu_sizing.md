# GPU sizing

> **Note (post-Groq switch):** As of the move to a hosted LLM provider (Groq),
> LLM inference no longer requires a local GPU. The sizing guidance below now
> applies only to the embedding worker (`worker-gpu`), which runs
> `intfloat/multilingual-e5-large` locally. The Ollama-specific sections
> remain valid as a reference for self-hosted deployments.

The v2t pipeline has exactly two GPU consumers:

1. **Ollama** running `qwen2.5:7b-instruct` (default quant: `q4_K_M`).
2. **`worker-gpu`** running `multilingual-e5-large` via sentence-transformers.

Everything else — extraction orchestration, clustering, canonicalization,
memory-graph inference, FastAPI — is CPU-only.

The recommended starting point is a single RTX 4080 (16 GB). The sections
below describe how to scale up and down from that baseline.

---

## On the recommended RTX 4080 (16 GB)

| Workload                         | VRAM    | Notes                                              |
|----------------------------------|---------|----------------------------------------------------|
| Ollama qwen2.5:7b-instruct       | 5–6 GB  | `q4_K_M` is the Ollama default and is what we ship |
| multilingual-e5-large            | ~2 GB   | Loaded once per `worker-gpu` process               |
| Headroom                         | ~8 GB   | OS, CUDA context, fragmentation, future models     |

Recommended runtime layout:

- One `ollama` container with `--gpus all`. Pre-pull weights via the
  `ollama-init` one-shot service in `docker-compose.yml`.
- One `worker-gpu` container with `--gpus all`, concurrency=1, so VRAM
  usage is deterministic.
- Both containers share the same GPU. There is no cross-process scheduling
  contention because Ollama goes idle between calls and e5-large encodes in
  short bursts.

Defaults that match this profile:

```bash
EMBEDDING_DEVICE=cuda
EMBEDDING_BATCH_SIZE=32
EMBEDDING_MAX_SEQ_LEN=512
OLLAMA_MODEL=qwen2.5:7b-instruct          # implicit q4_K_M
```

---

## Downsizing to a 12 GB GPU (e.g., RTX 4070 Ti)

VRAM budget gets tight. Apply:

- Switch Ollama to `q4_0` (or `q3_K_M` if you accept slightly worse JSON
  faithfulness): `ollama pull qwen2.5:7b-instruct-q4_0`, then set
  `OLLAMA_MODEL=qwen2.5:7b-instruct-q4_0`.
- Keep `EMBEDDING_DEVICE=cuda` but set worker-gpu concurrency=1.
- `EMBEDDING_BATCH_SIZE=16`.
- Disable any other GPU workloads on the host.

Expected footprint: ~4 GB (Ollama) + ~2 GB (e5-large) + ~5 GB headroom.

---

## Downsizing to an 8 GB GPU (e.g., RTX 3060)

The model and embedder cannot both fit on device with sane headroom. Move
embedding to CPU:

```bash
EMBEDDING_DEVICE=cpu
EMBEDDING_BATCH_SIZE=16
OLLAMA_MODEL=qwen2.5:7b-instruct-q4_0
```

Drop the GPU reservation from `worker-gpu` in `docker-compose.yml` (or fold
the embedding task into `worker-cpu` by removing the `gpu.heavy` queue
binding). Expect roughly **3× the embedding latency** of the RTX 4080 path,
and an unchanged Ollama latency.

If even Ollama doesn't fit, drop to a 3B model
(`OLLAMA_MODEL=qwen2.5:3b-instruct`); JSON quality degrades but the pipeline
still works.

---

## Scaling up to 24 GB+ (A10 / 4090 / L40)

You now have headroom to either run a larger context or parallelize more.

- Raise Ollama context length: pass `extra={"options": {"num_ctx": 8192}}`
  from `OllamaClient` callsites if you need to ingest longer transcripts in
  a single extraction pass. Defaults to 4096.
- Raise `EMBEDDING_BATCH_SIZE` to 64 or 128 — e5-large scales well with
  batch size until you hit ~6 GB activations.
- Increase Ollama parallelism: set `OLLAMA_NUM_PARALLEL=4` (or higher) so a
  single replica can serve concurrent chat completions. Watch VRAM during
  the first ramp-up; each parallel slot allocates additional KV cache.
- On L40 / 4090 you have enough VRAM to also keep a re-ranker model
  resident in `worker-gpu` if you decide to bolt one onto `/search` later.

---

## Latency expectations on RTX 4080

Indicative figures from the platform's reference workload (Indian insurance
support calls, 6–10 customer questions per call, q4_K_M Qwen2.5-7B).
Measure your own numbers with `python -m app.scripts.benchmark` —
production traffic, prompt length and quantization all move the needle.

| Stage                                 | Latency / throughput               |
|---------------------------------------|------------------------------------|
| Ollama time-to-first-token (TTFT)     | ~0.4 – 0.8 s                       |
| Ollama full extraction call           | ~1.5 – 3.0 s per call              |
| e5-large embedding throughput         | ~120 items/s at batch=32           |
| HDBSCAN on 50 000 1024-d vectors      | ~6 – 10 s (single-threaded)        |
| pgvector `ivfflat` top-10 search      | < 30 ms warm                       |
| FastAPI `/search` end-to-end (warm)   | ~80 – 120 ms                       |

These numbers are **indicative**, not guaranteed. They assume Ollama is
already loaded (i.e., not a cold-start) and the e5-large weights are warm
in VRAM.
