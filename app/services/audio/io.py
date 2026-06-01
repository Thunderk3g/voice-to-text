"""
Audio I/O — fetch source audio to a local temp path and upload pipeline
artifacts back to object storage.

Supported URI schemes:
- ``minio://bucket/key`` — pulled via the MinIO client (configured by settings)
- ``s3://bucket/key``    — fetched with boto3 if available, otherwise via MinIO
                           client pointed at the S3-compatible endpoint
- ``file://abs/path``    — local filesystem
- bare path (``/x/y.wav`` or ``C:\\foo.wav``) — local filesystem

The MinIO client is lazy + module-cached so importing this module is cheap.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from app.core.config import get_settings

log = structlog.get_logger(__name__)

_minio_client: Any | None = None


def _get_minio_client() -> Any:
    """Lazy MinIO client. Imported here so audio.io does not require minio
    to be installed at module load time (e.g. for unit tests)."""
    global _minio_client
    if _minio_client is not None:
        return _minio_client

    from minio import Minio  # local import — avoid import cost on cold start

    s = get_settings()
    _minio_client = Minio(
        endpoint=s.minio_endpoint,
        access_key=s.minio_access_key.get_secret_value(),
        secret_key=s.minio_secret_key.get_secret_value(),
        secure=s.minio_secure,
    )
    return _minio_client


def _split_bucket_key(parsed_netloc: str, parsed_path: str) -> tuple[str, str]:
    """Convert ``netloc='bucket', path='/key/inner'`` to ``(bucket, key/inner)``."""
    bucket = parsed_netloc
    key = parsed_path.lstrip("/")
    if not bucket or not key:
        raise ValueError(f"Invalid object URI: netloc={parsed_netloc!r} path={parsed_path!r}")
    return bucket, key


def _ensure_bucket(client: Any, bucket: str) -> None:
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except Exception as exc:  # pragma: no cover - best-effort
        log.warning("minio_ensure_bucket_failed", bucket=bucket, error=str(exc))


def download_to_temp(uri: str) -> str:
    """Download ``uri`` to a local temp file and return the absolute path.

    For ``file://`` and bare paths this returns the original path unchanged
    (no copy needed). For object-store URIs the file is materialized under
    ``tempfile.gettempdir()`` and the caller is responsible for cleanup.
    """
    if not uri:
        raise ValueError("uri must be a non-empty string")

    parsed = urlparse(uri)
    scheme = (parsed.scheme or "").lower()

    # Local filesystem cases.
    if scheme in ("", "file"):
        local = parsed.path if scheme == "file" else uri
        # On Windows, file:///C:/foo leaves path='/C:/foo' — strip the leading /.
        if scheme == "file" and os.name == "nt" and local.startswith("/") and len(local) > 2 and local[2] == ":":
            local = local[1:]
        if not Path(local).exists():
            raise FileNotFoundError(f"local audio path not found: {local}")
        log.debug("audio_local_path", uri=uri, path=local)
        return str(Path(local).resolve())

    if scheme not in ("minio", "s3"):
        raise ValueError(f"Unsupported URI scheme: {scheme!r}")

    bucket, key = _split_bucket_key(parsed.netloc, parsed.path)
    suffix = Path(key).suffix or ".bin"
    fd, tmp_path = tempfile.mkstemp(prefix="v2t_audio_", suffix=suffix)
    os.close(fd)

    client = _get_minio_client()
    log.info("audio_download_start", uri=uri, bucket=bucket, key=key, dest=tmp_path)
    try:
        client.fget_object(bucket, key, tmp_path)
    except Exception:
        # Don't leave a zero-byte temp file behind on failure.
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    log.info("audio_download_done", path=tmp_path, size=os.path.getsize(tmp_path))
    return tmp_path


def upload_artifact(local_path: str, key: str, bucket: str | None = None) -> str:
    """Upload a local file to MinIO under ``bucket/key`` and return the URI.

    ``bucket`` defaults to ``settings.minio_bucket_artifacts``.
    """
    s = get_settings()
    bucket = bucket or s.minio_bucket_artifacts
    p = Path(local_path)
    if not p.exists():
        raise FileNotFoundError(f"artifact not found: {local_path}")

    client = _get_minio_client()
    _ensure_bucket(client, bucket)
    log.info("artifact_upload_start", path=str(p), bucket=bucket, key=key)
    client.fput_object(bucket, key, str(p))
    uri = f"minio://{bucket}/{key}"
    log.info("artifact_upload_done", uri=uri, size=p.stat().st_size)
    return uri


def save_upload(local_path: str, *, bucket: str, key: str) -> str:
    """Upload a freshly-received file to an explicit ``bucket/key`` and return the URI.

    Mirrors :func:`upload_artifact` but targets an explicit bucket (no default).
    The ``audio-raw`` bucket is NOT created by minio-setup, so ensuring the
    bucket exists here is essential for the upload ingest path.
    """
    p = Path(local_path)
    if not p.exists():
        raise FileNotFoundError(f"upload not found: {local_path}")

    client = _get_minio_client()
    _ensure_bucket(client, bucket)
    log.info("upload_save_start", path=str(p), bucket=bucket, key=key)
    client.fput_object(bucket, key, str(p))
    uri = f"minio://{bucket}/{key}"
    log.info("upload_save_done", uri=uri, size=p.stat().st_size)
    return uri


def cleanup_temp(path: str) -> None:
    """Best-effort cleanup helper for files produced by ``download_to_temp``."""
    try:
        if path and Path(path).exists() and tempfile.gettempdir() in str(Path(path).resolve().parents):
            os.remove(path)
    except Exception as exc:  # pragma: no cover
        log.debug("cleanup_temp_failed", path=path, error=str(exc))


__all__ = ["download_to_temp", "upload_artifact", "save_upload", "cleanup_temp"]
