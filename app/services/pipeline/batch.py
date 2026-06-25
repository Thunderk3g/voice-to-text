"""Reusable, testable core for the parallel batch processor.

Pure scheduling/planning logic — no audio, model, network, or process pools here
(those live in ``app.scripts.batch_process``). Kept import-light so it is safe to
import inside spawned worker processes on macOS/Windows.

Design:
  * STT is CPU-bound  -> a PROCESS pool, one cached model per worker (see script).
  * LLM analysis is IO-bound -> high async concurrency in the main process.
  * Each stage is resumable by OUTPUT-FILE existence (crash-safe, no state file).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

# Rough per-process resident size (GB) of a faster-whisper int8 model. Used only
# to keep a RAM-bounded default worker count so a MacBook does not swap to death.
_MODEL_GB: dict[str, float] = {
    "tiny": 0.3, "base": 0.5, "small": 1.0, "medium": 2.0,
    "large-v2": 3.0, "large-v3": 3.0,
}


@dataclass(frozen=True)
class ConcurrencyPlan:
    engine: str
    stt_workers: int      # parallel STT processes (CPU) or 1 for a Metal GPU engine
    stt_threads: int      # intra-op threads per STT worker (OMP_NUM_THREADS)
    analyze_concurrency: int  # concurrent in-flight Groq calls (IO-bound)


def plan_concurrency(
    cpu_count: int | None = None,
    *,
    engine: str = "whisper",
    model: str = "base",
    ram_gb: float | None = None,
    analyze_concurrency: int | None = None,
) -> ConcurrencyPlan:
    """Pick safe default parallelism for the host.

    - ``mlx`` (Apple Metal): a single process owns the GPU; concurrency is internal,
      so ``stt_workers == 1`` and all cores feed it.
    - ``whisper`` (CPU): workers bounded by cores (leaving headroom) AND, when a RAM
      hint is given, by ``ram_gb * 0.7 / model_footprint`` so we never oversubscribe
      memory. ``stt_threads`` splits the remaining cores across workers.
    """
    cpu = cpu_count or os.cpu_count() or 4
    analyze = analyze_concurrency if analyze_concurrency and analyze_concurrency > 0 else 8

    if engine == "mlx":
        return ConcurrencyPlan(engine=engine, stt_workers=1, stt_threads=max(1, cpu),
                               analyze_concurrency=analyze)

    by_cpu = max(1, cpu - 1)  # leave a core for the OS + the analyze loop
    if ram_gb:
        model_gb = _MODEL_GB.get(model, 1.0)
        by_ram = max(1, int((ram_gb * 0.7) // model_gb))
        workers = max(1, min(by_cpu, by_ram))
    else:
        workers = max(1, cpu // 2)  # conservative without a RAM hint (models are heavy)
    threads = max(1, cpu // workers)
    return ConcurrencyPlan(engine=engine, stt_workers=workers, stt_threads=threads,
                           analyze_concurrency=analyze)


def done_keys(out_dir: str | Path, *, suffix: str = ".json") -> set[str]:
    """Stems of already-produced outputs in ``out_dir`` (the resume checkpoint)."""
    d = Path(out_dir)
    if not d.exists():
        return set()
    return {p.name[: -len(suffix)] for p in d.glob(f"*{suffix}")}


def pending_items(
    items: Iterable[Any],
    out_dir: str | Path,
    *,
    key: Callable[[Any], str] = str,
    suffix: str = ".json",
) -> list[Any]:
    """Items whose ``key`` has no corresponding ``<key><suffix>`` file in ``out_dir``."""
    done = done_keys(out_dir, suffix=suffix)
    return [it for it in items if key(it) not in done]


async def run_async_pool(
    items: list[Any],
    worker: Callable[[Any], Awaitable[Any]],
    *,
    concurrency: int,
    on_result: Callable[[Any, Any, BaseException | None], None] | None = None,
) -> list[tuple[Any, Any, BaseException | None]]:
    """Run ``worker(item)`` over ``items`` with a bounded concurrency semaphore.

    Per-item exceptions are captured (never abort the batch); each result tuple is
    ``(item, value_or_None, error_or_None)``. ``on_result`` fires as each completes
    (e.g. to write a per-item output file immediately for crash-safety).
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(it: Any) -> tuple[Any, Any, BaseException | None]:
        async with sem:
            try:
                val = await worker(it)
                err: BaseException | None = None
            except Exception as exc:  # noqa: BLE001 — isolate one bad item
                val, err = None, exc
            if on_result is not None:
                on_result(it, val, err)
            return (it, val, err)

    return await asyncio.gather(*[_one(it) for it in items])


__all__ = [
    "ConcurrencyPlan",
    "plan_concurrency",
    "done_keys",
    "pending_items",
    "run_async_pool",
]
