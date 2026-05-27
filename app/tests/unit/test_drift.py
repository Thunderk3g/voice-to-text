"""
Unit tests for compute_drift — growth rate formula + ranking.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.clustering.drift import compute_drift


def _row(*, recent, current, label="x"):
    return {
        "cluster_id": uuid4(),
        "label": label,
        "first_seen": datetime.now(timezone.utc) - timedelta(days=30),
        "current_size": current,
        "recent_size": recent,
    }


@pytest.mark.asyncio
async def test_growth_rate_against_baseline():
    rows = [
        _row(recent=5, current=10, label="A"),     # baseline=5,  rate=1.0
        _row(recent=20, current=30, label="B"),    # baseline=10, rate=2.0  ← top
        _row(recent=1, current=100, label="C"),    # min_recent filter drops it
        _row(recent=3, current=3, label="D"),      # baseline=0  → rate=3.0
    ]

    async def fetch(window_days):
        assert window_days == 7
        return rows

    out = await compute_drift(fetch, window_days=7, top_n=10, min_recent=3)

    labels = [r["label"] for r in out]
    # 'C' filtered by min_recent=3
    assert "C" not in labels
    # 'D' (rate 3.0) ranks above 'B' (rate 2.0) above 'A' (rate 1.0)
    assert labels == ["D", "B", "A"]

    for r in out:
        assert r["window_days"] == 7
        assert r["recent_size"] >= 3
        assert r["baseline_size"] == r["current_size"] - r["recent_size"]


@pytest.mark.asyncio
async def test_empty_input_returns_empty():
    async def fetch(window_days):
        return []

    out = await compute_drift(fetch, window_days=7)
    assert out == []


@pytest.mark.asyncio
async def test_top_n_limits_output():
    rows = [_row(recent=10, current=20 + i, label=f"c{i}") for i in range(15)]

    async def fetch(window_days):
        return rows

    out = await compute_drift(fetch, window_days=7, top_n=5, min_recent=1)
    assert len(out) == 5


@pytest.mark.asyncio
async def test_zero_recent_filtered():
    rows = [_row(recent=0, current=10, label="zero")]

    async def fetch(window_days):
        return rows

    out = await compute_drift(fetch, window_days=7, min_recent=1)
    assert out == []
