"""
OpenTelemetry tracing + Prometheus metrics helpers.

`init_tracing(service_name)` sets up an OTLP exporter when configured;
otherwise it is a no-op. Prometheus counters/histograms used across the
pipeline live in `metrics`.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from app.core.config import get_settings


# ----- Pipeline stage counters ------------------------------------------
calls_ingested = Counter(
    "v2t_calls_ingested_total",
    "Calls accepted into the pipeline.",
)
transcripts_loaded = Counter(
    "v2t_transcripts_loaded_total",
    "Pre-labeled transcripts loaded directly into the pipeline.",
    labelnames=("status",),
)
sarvam_transcribed = Counter(
    "v2t_sarvam_transcribed_total",
    "Audio calls transcribed via Sarvam.ai.",
    labelnames=("status",),
)
extraction_processed = Counter(
    "v2t_extraction_processed_total",
    "Utterances completed LLM extraction.",
    labelnames=("status",),
)
embeddings_generated = Counter(
    "v2t_embeddings_generated_total",
    "Embeddings generated.",
)
clusters_assigned = Counter(
    "v2t_clusters_assigned_total",
    "Cluster assignments performed.",
    labelnames=("mode",),  # incremental | batch
)

stage_duration_seconds = Histogram(
    "v2t_pipeline_stage_duration_seconds",
    "Wall-clock duration of pipeline stages.",
    labelnames=("stage",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)


def init_tracing(service_name: str | None = None) -> None:
    """Initialize OTel tracing if an OTLP endpoint is configured."""
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint:
        return

    name = service_name or settings.otel_service_name
    resource = Resource.create({"service.name": name, "service.namespace": "v2t"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer(name: str):
    return trace.get_tracer(name)
