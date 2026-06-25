"""Unit tests for the parallel batch core (planning, resume, bounded async pool)."""
from __future__ import annotations

import asyncio

import pytest

from app.services.pipeline.batch import (
    ConcurrencyPlan,
    done_keys,
    pending_items,
    plan_concurrency,
    run_async_pool,
)


def test_plan_whisper_cpu_defaults():
    p = plan_concurrency(8, engine="whisper", model="base")
    assert p.stt_workers == 4 and p.stt_threads == 2
    assert p.analyze_concurrency == 8
    assert p.stt_workers * p.stt_threads <= 8 + p.stt_workers  # no gross oversubscription


def test_plan_mlx_single_process_owns_gpu():
    p = plan_concurrency(10, engine="mlx", model="large-v3")
    assert p.stt_workers == 1 and p.stt_threads == 10


def test_plan_ram_bounds_workers_for_big_model():
    # 8 GB RAM, large-v3 (~3 GB/proc) -> 8*0.7//3 == 1 worker even on many cores.
    p = plan_concurrency(16, engine="whisper", model="large-v3", ram_gb=8)
    assert p.stt_workers == 1


def test_plan_analyze_concurrency_override():
    p = plan_concurrency(4, analyze_concurrency=24)
    assert p.analyze_concurrency == 24


def test_pending_items_skips_done(tmp_path):
    (tmp_path / "111.json").write_text("{}", encoding="utf-8")
    (tmp_path / "222.json").write_text("{}", encoding="utf-8")
    items = [("u1", "111"), ("u2", "222"), ("u3", "333")]
    pend = pending_items(items, tmp_path, key=lambda it: it[1])
    assert pend == [("u3", "333")]
    assert done_keys(tmp_path) == {"111", "222"}


def test_pending_items_all_when_dir_missing(tmp_path):
    missing = tmp_path / "nope"
    assert pending_items(["a", "b"], missing) == ["a", "b"]


@pytest.mark.asyncio
async def test_run_async_pool_bounded_isolates_errors():
    active = 0
    peak = 0
    seen: list = []

    async def worker(it):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if it == "boom":
            raise ValueError("bad item")
        return it.upper()

    items = ["a", "b", "boom", "c", "d"]
    results = await run_async_pool(
        items, worker, concurrency=2, on_result=lambda it, v, e: seen.append((it, e is None))
    )

    assert peak <= 2  # concurrency cap respected
    by_item = {it: (v, e) for it, v, e in results}
    assert by_item["a"][0] == "A" and by_item["a"][1] is None
    assert by_item["boom"][0] is None and isinstance(by_item["boom"][1], ValueError)
    assert len(seen) == 5  # on_result fired for every item incl. the failing one
