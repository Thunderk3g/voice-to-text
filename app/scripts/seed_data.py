"""
Seed the v2t platform with the bundled Servo-AI sample transcripts.

Each ``data/sample_transcripts/*.json`` file is POSTed to the API's
``/ingest`` endpoint exactly the way a real Servo-AI handoff would arrive.
The Celery pipeline then runs extraction -> embedding -> clustering ->
canonicalization -> memory edges.

Usage::

    python -m app.scripts.seed_data --api-url http://localhost:8080
    python -m app.scripts.seed_data --minio                # upload to MinIO
    python -m app.scripts.seed_data --dir /path/to/transcripts

The script is idempotent: a hash of ``filename + first text chunk`` is
recorded in the state file (default ``data/sample_transcripts/.seeded.json``).
Re-running with the same state file is a no-op.

The async coroutine ``run`` is importable so tests can exercise the script
without spawning a subprocess.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_API_URL = "http://localhost:8080"
DEFAULT_DIR = Path("data/sample_transcripts")
DEFAULT_STATE_FILE = DEFAULT_DIR / ".seeded.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _first_text_chunk(payload: list[dict[str, Any]] | dict[str, Any]) -> str:
    """Return the first ``text`` field of the transcript, used for hashing.

    Accepts either a list of utterances or a dict that wraps them.
    """
    items: list[dict[str, Any]]
    if isinstance(payload, dict):
        items = payload.get("utterances") or payload.get("segments") or []
    else:
        items = payload

    if not items:
        return ""
    first = items[0] or {}
    return str(first.get("text", ""))[:512]


def _hash_for(path: Path, payload: list[dict[str, Any]] | dict[str, Any]) -> str:
    """``sha1(filename + first_text_chunk)`` — stable per-file fingerprint."""
    h = hashlib.sha1()
    h.update(path.name.encode("utf-8"))
    h.update(b"\0")
    h.update(_first_text_chunk(payload).encode("utf-8"))
    return h.hexdigest()


def _load_state(state_file: Path) -> set[str]:
    """Load the set of already-seeded hashes from disk."""
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
        logger.warning("seed_state_unexpected_shape", path=str(state_file))
        return set()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("seed_state_load_failed", path=str(state_file), error=str(exc))
        return set()


def _save_state(state_file: Path, seen: set[str]) -> None:
    """Persist the set of seeded hashes as a sorted JSON list."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(sorted(seen), indent=2),
        encoding="utf-8",
    )


def _file_to_uri(path: Path, *, use_minio: bool, bucket: str) -> str:
    """Build the ``source_uri`` we hand to ``/ingest``."""
    if use_minio:
        # Object key keeps the filename so it's debuggable from the MinIO UI.
        return f"minio://{bucket}/sample_transcripts/{path.name}"
    return path.resolve().as_uri()  # file:///abs/path


async def _upload_to_minio(path: Path, key: str, bucket: str) -> None:
    """Best-effort MinIO upload. Imports lazily so unit tests don't need minio.

    Raises any exception encountered so the caller can decide how to count it.
    """
    from minio import Minio  # local import — keeps import-time cheap

    from app.core.config import get_settings

    s = get_settings()
    client = Minio(
        endpoint=s.minio_endpoint,
        access_key=s.minio_access_key.get_secret_value(),
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
    )
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    await asyncio.to_thread(client.fput_object, bucket, key, str(path))


# ---------------------------------------------------------------------------
# Result accounting
# ---------------------------------------------------------------------------
@dataclass
class SeedSummary:
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "uploaded": self.uploaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Core async entrypoint (importable)
# ---------------------------------------------------------------------------
async def run(
    *,
    api_url: str = DEFAULT_API_URL,
    directory: Path = DEFAULT_DIR,
    state_file: Path | None = None,
    use_minio: bool = False,
    bucket: str | None = None,
    metadata: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> SeedSummary:
    """Ingest every ``*.json`` file in ``directory`` into the v2t API.

    Parameters mirror the CLI flags. ``client`` may be injected from tests.
    """
    state_path = state_file if state_file is not None else (directory / ".seeded.json")
    seen = _load_state(state_path)
    summary = SeedSummary()

    files = sorted(p for p in directory.glob("*.json") if not p.name.startswith("."))
    if not files:
        logger.warning("seed_no_files_found", directory=str(directory))
        return summary

    payload_meta = metadata or {"campaign": "seed", "channel": "inbound"}

    # Resolve bucket lazily to avoid forcing Settings load for non-minio runs.
    if use_minio:
        if bucket is None:
            from app.core.config import get_settings

            bucket = get_settings().minio_bucket_transcripts

    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        base_url=api_url,
        timeout=httpx.Timeout(30.0),
    )

    try:
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                summary.failed += 1
                summary.errors.append(f"{path.name}: read/parse error: {exc}")
                logger.error("seed_parse_failed", file=str(path), error=str(exc))
                continue

            digest = _hash_for(path, payload)
            if digest in seen:
                summary.skipped += 1
                logger.info("seed_skip_idempotent", file=path.name, hash=digest)
                continue

            if use_minio:
                try:
                    await _upload_to_minio(
                        path,
                        key=f"sample_transcripts/{path.name}",
                        bucket=bucket or "transcripts",
                    )
                except Exception as exc:  # noqa: BLE001
                    summary.failed += 1
                    summary.errors.append(f"{path.name}: minio upload failed: {exc}")
                    logger.error("seed_minio_upload_failed", file=path.name, error=str(exc))
                    continue

            source_uri = _file_to_uri(path, use_minio=use_minio, bucket=bucket or "transcripts")
            body = {
                "source_uri": source_uri,
                "is_transcript": True,
                "metadata": payload_meta,
            }

            try:
                resp = await http_client.post("/ingest", json=body)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                summary.failed += 1
                summary.errors.append(f"{path.name}: POST /ingest failed: {exc}")
                logger.error("seed_ingest_failed", file=path.name, error=str(exc))
                continue

            seen.add(digest)
            summary.uploaded += 1
            logger.info(
                "seed_ingest_ok",
                file=path.name,
                status=resp.status_code,
                source_uri=source_uri,
            )

        _save_state(state_path, seen)
    finally:
        if owns_client:
            await http_client.aclose()

    return summary


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="app.scripts.seed_data",
        description="Seed v2t with the bundled Servo-AI sample transcripts.",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--dir", default=str(DEFAULT_DIR), dest="directory")
    parser.add_argument(
        "--minio",
        action="store_true",
        help="Upload transcripts to MinIO and pass minio:// URIs to /ingest.",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="Override the MinIO bucket (defaults to settings.minio_bucket_transcripts).",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to the idempotency state file (defaults to <dir>/.seeded.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    directory = Path(args.directory)
    state_file = Path(args.state_file) if args.state_file else None

    summary = asyncio.run(
        run(
            api_url=args.api_url,
            directory=directory,
            state_file=state_file,
            use_minio=args.minio,
            bucket=args.bucket,
        )
    )

    print("--- seed_data summary ---")
    print(f"  uploaded : {summary.uploaded}")
    print(f"  skipped  : {summary.skipped}")
    print(f"  failed   : {summary.failed}")
    if summary.errors:
        print("  errors:")
        for line in summary.errors:
            print(f"    - {line}")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
