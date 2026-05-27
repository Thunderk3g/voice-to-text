"""
Smoke test for ``app.scripts.seed_data.run``.

This is an integration test in the sense that it exercises the whole script
end to end against the bundled Servo-AI sample transcripts, but the HTTP
layer is mocked with ``respx`` so it does not need a running API.

Assertions:

1. Every ``*.json`` in ``data/sample_transcripts`` triggers exactly one
   POST to ``/ingest``.
2. The state file is written in the expected JSON-list-of-hashes shape.
3. Re-running with the same state file is a no-op (all files are skipped).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.scripts.seed_data import run

# The canonical sample directory is committed to the repo.
SAMPLE_DIR = Path(__file__).resolve().parents[3] / "data" / "sample_transcripts"
EXPECTED_FILE_COUNT = 6


def _sample_files() -> list[Path]:
    return sorted(
        p for p in SAMPLE_DIR.glob("*.json") if not p.name.startswith(".")
    )


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "seeded.json"


@pytest.mark.asyncio
async def test_seed_posts_each_sample_once(state_file: Path) -> None:
    """Every bundled sample triggers one POST /ingest call."""
    files = _sample_files()
    assert len(files) == EXPECTED_FILE_COUNT, (
        f"Expected {EXPECTED_FILE_COUNT} sample transcripts, found {len(files)}"
    )

    api_url = "http://api.test"
    async with httpx.AsyncClient(base_url=api_url, timeout=5.0) as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"{api_url}/ingest").mock(
                return_value=httpx.Response(202, json={"call_id": "00000000-0000-0000-0000-000000000001"})
            )

            summary = await run(
                api_url=api_url,
                directory=SAMPLE_DIR,
                state_file=state_file,
                use_minio=False,
                client=client,
            )

    assert summary.uploaded == EXPECTED_FILE_COUNT
    assert summary.skipped == 0
    assert summary.failed == 0
    assert route.call_count == EXPECTED_FILE_COUNT

    # Every request must carry is_transcript=true and a file:// URI.
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        assert body["is_transcript"] is True
        assert body["source_uri"].startswith("file://")
        assert body["metadata"]["campaign"] == "seed"

    # State file must exist with one hash per sample file.
    assert state_file.exists()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    assert all(isinstance(x, str) for x in saved)
    assert len(saved) == EXPECTED_FILE_COUNT
    # Hashes are sha1 hexdigests => 40 chars.
    assert all(len(x) == 40 for x in saved)


@pytest.mark.asyncio
async def test_seed_is_idempotent(state_file: Path) -> None:
    """Re-running with the same state file skips every sample."""
    api_url = "http://api.test"

    # First run — populate the state file by mocking the API.
    async with httpx.AsyncClient(base_url=api_url, timeout=5.0) as client:
        with respx.mock(assert_all_called=False) as mock:
            mock.post(f"{api_url}/ingest").mock(
                return_value=httpx.Response(202, json={"call_id": "00000000-0000-0000-0000-000000000001"})
            )
            first = await run(
                api_url=api_url,
                directory=SAMPLE_DIR,
                state_file=state_file,
                use_minio=False,
                client=client,
            )

    assert first.uploaded == EXPECTED_FILE_COUNT

    # Second run — no /ingest mock at all. If the script tries to POST,
    # respx will raise (pass-through is off by default), which is exactly
    # the failure we want.
    async with httpx.AsyncClient(base_url=api_url, timeout=5.0) as client:
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"{api_url}/ingest").mock(
                return_value=httpx.Response(500, json={"detail": "should not be called"})
            )
            second = await run(
                api_url=api_url,
                directory=SAMPLE_DIR,
                state_file=state_file,
                use_minio=False,
                client=client,
            )

    assert second.uploaded == 0
    assert second.skipped == EXPECTED_FILE_COUNT
    assert second.failed == 0
    assert route.call_count == 0
