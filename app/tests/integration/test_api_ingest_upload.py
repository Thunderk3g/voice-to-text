"""
Integration tests for POST /ingest/upload (multipart file ingest).

We avoid all external services:
  - ``get_db`` is overridden with a fake async session (no Postgres)
  - ``app.api.routes.ingest.send`` is patched (no Celery / Redis broker)
  - ``app.api.routes.ingest.save_upload`` is patched (no MinIO)

The shared contract verified here (frontend relies on it):
  - field name ``file`` (+ optional ``campaign``, ``channel``, ``is_transcript``)
  - success 202: {"call_id", "source_uri", "is_transcript"}
  - 400 empty file, 415 unsupported extension.
"""

from __future__ import annotations

import io
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import dependencies as deps
from app.api.main import create_app


class _FakeCall:
    """Stand-in for the ORM Call row; gets an id on flush()."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        self.id = uuid4()


class _FakeSession:
    """Minimal AsyncSession surface used by the ingest helper."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def saved_buckets() -> list[str]:
    return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, saved_buckets: list[str]) -> TestClient:
    from app.api.routes import ingest as ingest_mod

    # No Celery broker.
    monkeypatch.setattr(ingest_mod, "send", lambda *a, **k: "fake-task-id")

    # No MinIO — capture the bucket used and return a fake URI.
    def _fake_save_upload(local_path: str, *, bucket: str, key: str) -> str:
        saved_buckets.append(bucket)
        return f"minio://{bucket}/{key}"

    monkeypatch.setattr(ingest_mod, "save_upload", _fake_save_upload)

    # No idempotency Redis traffic on the JSON path (not used here, but safe).
    async def _no_lookup(*a: Any, **k: Any) -> None:
        return None

    async def _no_remember(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(ingest_mod.idempotency, "lookup", _no_lookup)
    monkeypatch.setattr(ingest_mod.idempotency, "remember", _no_remember)

    async def _override_get_db():
        yield _FakeSession()

    app = create_app()
    app.dependency_overrides[deps.get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_upload_audio_wav_returns_202(client: TestClient, saved_buckets: list[str]) -> None:
    files = {"file": ("call123.wav", io.BytesIO(b"RIFFfake-audio-bytes"), "audio/wav")}
    response = client.post("/ingest/upload", files=files, data={"campaign": "q2-renewals"})

    assert response.status_code == 202, response.text
    body = response.json()
    assert "call_id" in body
    assert body["source_uri"].startswith("minio://")
    assert body["is_transcript"] is False
    # Audio goes to the audio-raw bucket.
    assert saved_buckets == ["audio-raw"]


def test_upload_transcript_json_sets_is_transcript(
    client: TestClient, saved_buckets: list[str]
) -> None:
    payload = b'{"utterances": []}'
    files = {"file": ("labels.json", io.BytesIO(payload), "application/json")}
    response = client.post("/ingest/upload", files=files)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["is_transcript"] is True
    # Transcript goes to the transcripts bucket.
    assert saved_buckets == ["transcripts"]


def test_upload_unsupported_extension_returns_415(client: TestClient) -> None:
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    response = client.post("/ingest/upload", files=files)

    assert response.status_code == 415, response.text
    assert response.json()["type"] == "unsupported_media_type"


def test_upload_empty_file_returns_400(client: TestClient) -> None:
    files = {"file": ("call.wav", io.BytesIO(b""), "audio/wav")}
    response = client.post("/ingest/upload", files=files)

    assert response.status_code == 400, response.text
    assert response.json()["type"] == "empty_file"
