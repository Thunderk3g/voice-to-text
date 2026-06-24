"""Ingest a deterministic slice of the Crux call corpus via /ingest/upload.

Reads the Crux duration index CSV (``duration_index.csv``), selects a
reproducible slice filtered by call duration and duration bucket, resolves each
selected row to its on-disk audio file, and uploads it to the running ingest
API tagged with ``channel="inbound"`` / ``campaign="crux-slice"``.

The point is to prove the analysis loop on a representative slice BEFORE any
corpus-scale ingestion spend. The slice selection (``select_slice``) is a pure
function with no network or filesystem side effects so it can be unit-tested in
isolation; all CSV reading and uploading happens inside ``run``/``main``.

Usage::

    python -m app.scripts.ingest_crux_slice
    python -m app.scripts.ingest_crux_slice --limit 500 --seed 42 --concurrency 4
    python -m app.scripts.ingest_crux_slice --index D:/crux_calls/.../duration_index.csv

Idempotent across re-runs: uploaded source paths are recorded in
``.ingested.json`` (next to the index CSV) and skipped next time.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_INDEX = Path("D:/crux_calls/dataset/voice_to_text_index/duration_index.csv")
DEFAULT_BUCKETS = "15-30s,30s+"
DEFAULT_MIN_DURATION = 30.0
DEFAULT_LIMIT = 500
DEFAULT_SEED = 42
DEFAULT_CONCURRENCY = 4


def select_slice(
    rows: list[dict],
    *,
    min_duration: float,
    buckets: set[str],
    limit: int,
    seed: int,
) -> list[dict]:
    """Pure, deterministic slice selector — no I/O.

    Keep only rows with ``duration_sec >= min_duration`` and (if ``buckets`` is
    non-empty) ``bucket in buckets``, then deterministically down-sample to at
    most ``limit`` rows using ``random.Random(seed)``.
    """
    elig = [
        r
        for r in rows
        if float(r.get("duration_sec") or 0) >= min_duration
        and (r.get("bucket") in buckets if buckets else True)
    ]
    rng = random.Random(seed)
    return elig if len(elig) <= limit else rng.sample(elig, limit)


def _state_file(index: Path) -> Path:
    return index.parent / ".ingested.json"


def _load_state(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        return set(json.loads(state_file.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_state(state_file: Path, seen: set[str]) -> None:
    state_file.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def _read_index(index: Path) -> list[dict]:
    with index.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _resolve_audio_path(row: dict) -> Path | None:
    """Prefer ``new_path`` if non-empty, else ``original_path``."""
    candidate = (row.get("new_path") or "").strip() or (row.get("original_path") or "").strip()
    return Path(candidate) if candidate else None


async def _upload_one(
    client: httpx.AsyncClient,
    path: Path,
    sem: asyncio.Semaphore,
) -> tuple[str, str | None]:
    """Upload one file; return (source-path, error-or-None)."""
    async with sem:
        try:
            with path.open("rb") as fh:
                resp = await client.post(
                    "/ingest/upload",
                    files={"file": (path.name, fh, "audio/mpeg")},
                    data={"channel": "inbound", "campaign": "crux-slice"},
                )
            resp.raise_for_status()
            return str(path), None
        except (httpx.HTTPError, OSError) as exc:
            return str(path), str(exc)


async def run(
    *,
    api_url: str,
    index: Path,
    min_duration: float,
    buckets: set[str],
    limit: int,
    seed: int,
    concurrency: int,
) -> dict[str, int]:
    rows = _read_index(index)
    selected = select_slice(
        rows, min_duration=min_duration, buckets=buckets, limit=limit, seed=seed
    )

    state_file = _state_file(index)
    seen = _load_state(state_file)

    pending: list[Path] = []
    missing = 0
    for row in selected:
        path = _resolve_audio_path(row)
        if path is None:
            missing += 1
            continue
        if str(path) in seen:
            continue
        pending.append(path)

    print(
        f"index: {len(rows)} rows | selected: {len(selected)} | "
        f"already ingested: {len(seen)} | unresolved: {missing} | uploading: {len(pending)}"
    )

    sem = asyncio.Semaphore(concurrency)
    uploaded = failed = 0
    async with httpx.AsyncClient(base_url=api_url, timeout=httpx.Timeout(120.0)) as client:
        for batch_start in range(0, len(pending), 50):
            batch = pending[batch_start : batch_start + 50]
            results = await asyncio.gather(*[_upload_one(client, p, sem) for p in batch])
            for src, err in results:
                if err is None:
                    uploaded += 1
                    seen.add(src)
                else:
                    failed += 1
                    print(f"  FAIL {src}: {err}")
            _save_state(state_file, seen)
            print(f"  progress: {uploaded} uploaded, {failed} failed")

    return {"uploaded": uploaded, "failed": failed, "missing": missing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.scripts.ingest_crux_slice")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="Path to duration_index.csv.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument(
        "--min-duration",
        type=float,
        default=DEFAULT_MIN_DURATION,
        help="Minimum call duration (seconds) to include.",
    )
    parser.add_argument(
        "--buckets",
        default=DEFAULT_BUCKETS,
        help="Comma-separated duration buckets to include.",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="Max number of calls to ingest."
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="RNG seed for reproducible sampling."
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args(argv)

    buckets = {b.strip() for b in args.buckets.split(",") if b.strip()}

    summary = asyncio.run(
        run(
            api_url=args.api_url,
            index=Path(args.index),
            min_duration=args.min_duration,
            buckets=buckets,
            limit=args.limit,
            seed=args.seed,
            concurrency=args.concurrency,
        )
    )
    print(f"--- crux slice ingest summary: {summary} ---")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
