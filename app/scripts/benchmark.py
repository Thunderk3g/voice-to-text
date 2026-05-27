"""
Synthetic benchmarks for the v2t platform.

Run all benchmarks::

    python -m app.scripts.benchmark

Skip individual stages::

    python -m app.scripts.benchmark --skip-clustering --skip-ollama

The benchmarks are intentionally synthetic so they don't depend on a fully
populated database — they exercise just the components whose performance we
care about end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Multilingual prompts used to seed `bench_embedding` — kept short so we can
# generate thousands of variations cheaply.
_MULTILINGUAL_SEEDS = [
    "claim status check kaise karein",
    "मेरी पॉलिसी का प्रीमियम कब due है",
    "policy maturity benefit calculation",
    "nominee update karna hai",
    "term plan ka surrender value kya hoga",
    "என் காப்பீட்டு கோரிக்கை எப்போது பணம் வழங்கப்படும்",
    "నా పాలసీ ప్రీమియం ఎప్పుడు చెల్లించాలి",
    "free look period in life insurance",
    "agent ne galat policy bech di mujhe",
    "cashless hospital kaise dhundhein",
]


# ---------------------------------------------------------------------------
# Bench result accounting
# ---------------------------------------------------------------------------
@dataclass
class BenchResult:
    name: str
    status: str  # "ok" | "skipped" | "error"
    metrics: dict[str, Any] = field(default_factory=dict)
    note: str | None = None

    def as_row(self) -> tuple[str, str, str]:
        if self.status != "ok":
            return self.name, self.status.upper(), self.note or ""
        rendered = ", ".join(f"{k}={v}" for k, v in self.metrics.items())
        return self.name, "OK", rendered


# ---------------------------------------------------------------------------
# Bench 1 — embedding throughput
# ---------------------------------------------------------------------------
async def bench_embedding(n: int = 1000) -> BenchResult:
    """Embed ``n`` random multilingual strings and report items/sec."""
    from app.services.embedding.e5 import EmbeddingService

    rng = random.Random(0xE5)
    texts: list[str] = []
    for _ in range(n):
        base = rng.choice(_MULTILINGUAL_SEEDS)
        # add a short noise suffix so cache lookups can't dominate the run
        suffix = "".join(rng.choices(string.ascii_lowercase, k=6))
        texts.append(f"{base} #{suffix}")

    try:
        svc = EmbeddingService()
        # Warm up the model so cold-start doesn't pollute the measurement.
        await svc.embed(texts[:8], role="passage")

        t0 = time.perf_counter()
        vectors = await svc.embed(texts, role="passage")
        elapsed = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        return BenchResult(
            name="embedding",
            status="error",
            note=f"{type(exc).__name__}: {exc}",
        )

    items_per_sec = n / elapsed if elapsed > 0 else 0.0
    dim = len(vectors[0]) if vectors else 0
    return BenchResult(
        name="embedding",
        status="ok",
        metrics={
            "n": n,
            "elapsed_s": round(elapsed, 3),
            "items_per_sec": round(items_per_sec, 1),
            "dim": dim,
        },
    )


# ---------------------------------------------------------------------------
# Bench 2 — HDBSCAN clustering wall-time
# ---------------------------------------------------------------------------
def bench_clustering(n: int = 10_000, dim: int = 1024) -> BenchResult:
    """Cluster ``n`` random normalized vectors and report wall-time + cluster count."""
    try:
        import hdbscan
    except ImportError as exc:
        return BenchResult(
            name="clustering",
            status="skipped",
            note=f"hdbscan not importable: {exc}",
        )

    rng = np.random.default_rng(0xC1)
    s = get_settings()

    vecs = rng.standard_normal((n, dim)).astype(np.float32)
    # L2-normalize so the synthetic data lives on the unit hypersphere, like
    # real e5 embeddings do.
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs /= norms

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=s.hdbscan_min_cluster_size,
        min_samples=s.hdbscan_min_samples,
        metric=s.hdbscan_metric,
        core_dist_n_jobs=-1,
    )

    t0 = time.perf_counter()
    try:
        labels = clusterer.fit_predict(vecs)
    except Exception as exc:  # noqa: BLE001
        return BenchResult(
            name="clustering",
            status="error",
            note=f"{type(exc).__name__}: {exc}",
        )
    elapsed = time.perf_counter() - t0

    n_clusters = int((labels.max() + 1) if labels.size else 0)
    n_noise = int((labels == -1).sum())
    return BenchResult(
        name="clustering",
        status="ok",
        metrics={
            "n": n,
            "dim": dim,
            "elapsed_s": round(elapsed, 3),
            "clusters": n_clusters,
            "noise": n_noise,
        },
    )


# ---------------------------------------------------------------------------
# Bench 3 — pgvector / API search latency at different top_k
# ---------------------------------------------------------------------------
async def bench_pgvector_search(
    *,
    api_url: str = "http://localhost:8080",
    top_ks: tuple[int, ...] = (10, 50, 200),
    query: str = "claim reject kyun hua",
    repeat: int = 5,
) -> BenchResult:
    """Issue ``POST /search`` against a live API and time each top_k."""
    async with httpx.AsyncClient(base_url=api_url, timeout=15.0) as client:
        try:
            health = await client.get("/healthz")
            if health.status_code != 200:
                return BenchResult(
                    name="pgvector_search",
                    status="skipped",
                    note=f"/healthz returned {health.status_code}",
                )
        except httpx.HTTPError as exc:
            return BenchResult(
                name="pgvector_search",
                status="skipped",
                note=f"API unreachable: {exc}",
            )

        metrics: dict[str, Any] = {}
        try:
            for k in top_ks:
                samples: list[float] = []
                for _ in range(repeat):
                    t0 = time.perf_counter()
                    resp = await client.post(
                        "/search",
                        json={"query": query, "top_k": k},
                    )
                    resp.raise_for_status()
                    samples.append((time.perf_counter() - t0) * 1000.0)
                metrics[f"p50_top{k}_ms"] = round(statistics.median(samples), 2)
                metrics[f"max_top{k}_ms"] = round(max(samples), 2)
        except httpx.HTTPError as exc:
            return BenchResult(
                name="pgvector_search",
                status="error",
                note=f"{type(exc).__name__}: {exc}",
            )

    return BenchResult(name="pgvector_search", status="ok", metrics=metrics)


# ---------------------------------------------------------------------------
# Bench 4 — Ollama extraction round-trip
# ---------------------------------------------------------------------------
async def bench_ollama_extraction(call_path: str | None = None) -> BenchResult:
    """Run a JSON-mode extraction against Ollama using a real sample transcript.

    Skips cleanly if Ollama is unreachable.
    """
    from app.prompts import EXTRACTION_SYSTEM, EXTRACTION_USER_TEMPLATE
    from app.services.llm.ollama_client import OllamaClient

    sample_path = Path(call_path or "data/sample_transcripts/call_001_hindi.json")
    if not sample_path.exists():
        return BenchResult(
            name="ollama_extraction",
            status="skipped",
            note=f"sample transcript not found: {sample_path}",
        )

    try:
        utterances = json.loads(sample_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return BenchResult(
            name="ollama_extraction",
            status="error",
            note=f"failed to read sample: {exc}",
        )

    # Build the user prompt with whatever template signature exists. We don't
    # know which keyword the prompt template expects, so we try the common
    # patterns; if none of them apply we fall back to a literal join.
    try:
        rendered_user = EXTRACTION_USER_TEMPLATE.format(utterances=utterances)
    except Exception:  # noqa: BLE001
        try:
            rendered_user = EXTRACTION_USER_TEMPLATE.format(
                transcript=json.dumps(utterances, ensure_ascii=False)
            )
        except Exception:  # noqa: BLE001
            rendered_user = json.dumps(utterances, ensure_ascii=False)

    client = OllamaClient()
    try:
        t0 = time.perf_counter()
        try:
            parsed = await client.chat_json(EXTRACTION_SYSTEM, rendered_user)
        except (httpx.HTTPError, OSError) as exc:
            return BenchResult(
                name="ollama_extraction",
                status="skipped",
                note=f"Ollama unreachable: {exc}",
            )
        elapsed = time.perf_counter() - t0
    finally:
        await client.aclose()

    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    return BenchResult(
        name="ollama_extraction",
        status="ok",
        metrics={
            "sample": sample_path.name,
            "elapsed_s": round(elapsed, 3),
            "questions_parsed": len(questions) if isinstance(questions, list) else 0,
        },
    )


# ---------------------------------------------------------------------------
# Pretty-printer
# ---------------------------------------------------------------------------
def _render_markdown_table(results: list[BenchResult]) -> str:
    rows = [r.as_row() for r in results]
    header = ("Benchmark", "Status", "Details")
    widths = [
        max(len(header[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(3)
    ]

    def _fmt(row: tuple[str, str, str]) -> str:
        return "| " + " | ".join(row[i].ljust(widths[i]) for i in range(3)) + " |"

    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    return "\n".join([_fmt(header), sep, *(_fmt(r) for r in rows)])


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
async def _async_main(args: argparse.Namespace) -> list[BenchResult]:
    results: list[BenchResult] = []
    results.append(await bench_embedding(n=args.embed_n))

    if args.skip_clustering:
        results.append(BenchResult("clustering", "skipped", note="--skip-clustering"))
    else:
        # HDBSCAN is CPU bound and synchronous; run it off the event loop.
        results.append(
            await asyncio.to_thread(
                bench_clustering, args.cluster_n, args.cluster_dim
            )
        )

    if args.skip_search:
        results.append(BenchResult("pgvector_search", "skipped", note="--skip-search"))
    else:
        results.append(await bench_pgvector_search(api_url=args.api_url))

    if args.skip_ollama:
        results.append(BenchResult("ollama_extraction", "skipped", note="--skip-ollama"))
    else:
        results.append(await bench_ollama_extraction(call_path=args.ollama_sample))

    return results


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.scripts.benchmark",
        description="Synthetic benchmarks for v2t.",
    )
    parser.add_argument("--api-url", default="http://localhost:8080")
    parser.add_argument("--embed-n", type=int, default=1000)
    parser.add_argument("--cluster-n", type=int, default=10_000)
    parser.add_argument("--cluster-dim", type=int, default=1024)
    parser.add_argument("--ollama-sample", default=None)
    parser.add_argument("--skip-clustering", action="store_true")
    parser.add_argument("--skip-search", action="store_true")
    parser.add_argument("--skip-ollama", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)
    results = asyncio.run(_async_main(args))

    print()
    print(_render_markdown_table(results))
    print()

    return 0 if all(r.status != "error" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
