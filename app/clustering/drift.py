"""
Cluster drift / emerging-topic detection.

We surface clusters whose recent growth (members added in the last
`window_days`) is large relative to their pre-window baseline. The shape
returned matches the `emerging_topics` field on `AnalyticsSummary`.

The data source is an injected async callable so this module is
DB-agnostic and easy to unit-test.
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable
from uuid import UUID

# fetch_cluster_growth(window_days) -> list[dict]
# Each dict must include:
#   "cluster_id":   UUID
#   "label":        str | None
#   "first_seen":   datetime
#   "current_size": int
#   "recent_size":  int          # members assigned within window_days
FetchClusterGrowth = Callable[[int], Awaitable[list[dict]]]


def _growth_rate(recent: int, baseline: int) -> float:
    """Recent additions over pre-window baseline; clipped for stability.

    A brand-new cluster (baseline == 0) yields a large but finite rate so
    it ranks high without dividing by zero.
    """
    if recent <= 0:
        return 0.0
    if baseline <= 0:
        # New cluster: full growth attributed to window. Scale by size.
        return float(recent)
    return float(recent) / float(baseline)


async def compute_drift(
    fetch_cluster_growth: FetchClusterGrowth,
    *,
    window_days: int = 7,
    top_n: int = 20,
    min_recent: int = 3,
) -> list[dict]:
    """Return ranked list of emerging clusters for the given window.

    Each output dict contains:
        cluster_id, label, first_seen, current_size,
        recent_size, baseline_size, growth_rate, window_days
    """
    raw = await fetch_cluster_growth(int(window_days))
    if not raw:
        return []

    enriched: list[dict] = []
    for row in raw:
        current = int(row.get("current_size", 0) or 0)
        recent = int(row.get("recent_size", 0) or 0)
        if recent < int(min_recent):
            continue
        baseline = max(0, current - recent)
        rate = _growth_rate(recent, baseline)

        first_seen = row.get("first_seen")
        if isinstance(first_seen, datetime):
            first_seen_out: datetime | str | None = first_seen
        else:
            first_seen_out = first_seen  # may be None or string already

        enriched.append(
            {
                "cluster_id": row.get("cluster_id"),
                "label": row.get("label"),
                "first_seen": first_seen_out,
                "current_size": current,
                "recent_size": recent,
                "baseline_size": baseline,
                "growth_rate": rate,
                "window_days": int(window_days),
            }
        )

    enriched.sort(
        key=lambda d: (d["growth_rate"], d["recent_size"]),
        reverse=True,
    )
    return enriched[: max(0, int(top_n))]


__all__ = ["compute_drift", "FetchClusterGrowth"]
