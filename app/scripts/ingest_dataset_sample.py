"""Ingest a reproducible random sample of dataset/*.mp3 via /ingest/upload.

Used to populate a representative DB for the Phase A granularity diagnostics
(`app.scripts.phase_a_diagnostics`) when no production DB is available.

Usage::

    python -m app.scripts.ingest_dataset_sample --n 500
    python -m app.scripts.ingest_dataset_sample --n 500 --seed 42 --api-url http://localhost:8080

Idempotent across re-runs: uploaded filenames are recorded in
``dataset/.ingested.json`` and skipped next time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_DIR = Path("dataset")
STATE_FILE = DEFAULT_DIR / ".ingested.json"


def _load_state(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    try:
        return set(json.loads(state_file.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_state(state_file: Path, seen: set[str]) -> None:
    state_file.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


async def _upload_one(
    client: httpx.AsyncClient,
    path: Path,
    sem: asyncio.Semaphore,
) -> tuple[str, str | None]:
    """Upload one file; return (filename, error-or-None)."""
    async with sem:
        try:
            with path.open("rb") as fh:
                resp = await client.post(
                    "/ingest/upload",
                    files={"file": (path.name, fh, "audio/mpeg")},
                    data={"campaign": "phase-a-diagnostics", "channel": "inbound"},
                )
            resp.raise_for_status()
            return path.name, None
        except httpx.HTTPError as exc:
            return path.name, str(exc)


async def run(
    *,
    api_url: str,
    directory: Path,
    n: int,
    seed: int,
    concurrency: int,
) -> dict[str, int]:
    seen = _load_state(STATE_FILE)
    all_files = sorted(directory.glob("*.mp3"))
    candidates = [p for p in all_files if p.name not in seen]

    rng = random.Random(seed)
    sample = candidates if len(candidates) <= n else rng.sample(candidates, n)

    print(f"dataset: {len(all_files)} files | already ingested: {len(seen)} | uploading: {len(sample)}")

    sem = asyncio.Semaphore(concurrency)
    uploaded = failed = 0
    async with httpx.AsyncClient(base_url=api_url, timeout=httpx.Timeout(120.0)) as client:
        for batch_start in range(0, len(sample), 50):
            batch = sample[batch_start : batch_start + 50]
            results = await asyncio.gather(*[_upload_one(client, p, sem) for p in batch])
            for name, err in results:
                if err is None:
                    uploaded += 1
                    seen.add(name)
                else:
                    failed += 1
                    print(f"  FAIL {name}: {err}")
            _save_state(STATE_FILE, seen)
            print(f"  progress: {uploaded} uploaded, {failed} failed")

    return {"uploaded": uploaded, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.scripts.ingest_dataset_sample")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--dir", default=str(DEFAULT_DIR), dest="directory")
    parser.add_argument("--n", type=int, default=500, help="Sample size to ingest.")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible sampling.")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)

    summary = asyncio.run(
        run(
            api_url=args.api_url,
            directory=Path(args.directory),
            n=args.n,
            seed=args.seed,
            concurrency=args.concurrency,
        )
    )
    print(f"--- ingest summary: {summary} ---")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
