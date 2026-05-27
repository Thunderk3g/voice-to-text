"""
Central settings loader.

All services in the platform read from a single Settings object so that
configuration is consistent and validated at startup. Settings are
populated from environment variables (or a .env file).

STT provider: Sarvam.ai. Long audio is chunked on silences (<30s per
chunk to fit Sarvam's sync REST envelope), transcribed in parallel,
stitched back with offset timestamps, then run through the on-board
speaker-assignment heuristic. Pre-transcribed JSON is still accepted via
/ingest with is_transcript=true.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level configuration for every service in the platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: Literal["local", "dev", "staging", "prod"] = "local"
    app_log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8080

    # ---- Postgres ----
    database_url: str = Field(
        default="postgresql+asyncpg://v2t:v2t_pw@postgres:5432/v2t",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://v2t:v2t_pw@postgres:5432/v2t",
        description="Sync DSN for Alembic.",
    )

    # ---- Redis / Celery ----
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # ---- MinIO ----
    minio_endpoint: str = "minio:9000"
    minio_access_key: SecretStr = SecretStr("minio_admin")
    minio_secret_key: SecretStr = SecretStr("minio_admin_pw")
    minio_secure: bool = False
    minio_bucket_audio: str = "audio-raw"
    minio_bucket_transcripts: str = "transcripts"
    minio_bucket_artifacts: str = "pipeline-artifacts"

    # ---- Sarvam.ai STT ----
    stt_provider: Literal["sarvam", "none"] = "sarvam"
    sarvam_api_key: SecretStr = SecretStr("")
    sarvam_stt_model: str = "saarika:v2.5"
    sarvam_language_code: str = "unknown"  # "unknown" auto-detects across Sarvam's Indic set
    sarvam_request_timeout_s: int = 60
    sarvam_chunk_duration_s: int = 25
    sarvam_chunk_overlap_ms: int = 200

    # ---- Ollama (OpenAI-compatible) ----
    ollama_base_url: str = "http://ollama:11434/v1"
    ollama_api_key: SecretStr = SecretStr("ollama")  # Ollama ignores; placeholder
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_max_tokens: int = 1024
    ollama_temperature: float = 0.1
    ollama_request_timeout_s: int = 180

    # ---- Embeddings ----
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_dim: int = 1024
    embedding_device: Literal["cuda", "cpu"] = "cuda"
    embedding_batch_size: int = 32
    embedding_max_seq_len: int = 512

    # ---- Clustering ----
    hdbscan_min_cluster_size: int = 8
    hdbscan_min_samples: int = 4
    hdbscan_metric: str = "euclidean"
    cluster_incremental_threshold: float = 0.78
    cluster_reassign_period: str = "24h"

    # ---- Memory graph ----
    memory_edge_top_k: int = 8
    memory_edge_min_sim: float = 0.55
    memory_edge_max_per_cluster: int = 6

    # ---- Observability ----
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "v2t-api"
    prometheus_port: int = 9100


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — call this from anywhere in the app."""
    return Settings()
