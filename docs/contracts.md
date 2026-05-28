# v2t — Inter-service contracts (READ ME if you're an agent)

This file is the source of truth for **module paths, type names, and function
signatures** that cross subsystem boundaries.

> **Pipeline scope:** STT and speaker diarization are handled upstream by
> **Servo AI**. This platform ingests speaker-labeled transcripts only.
> LLM inference is provided by any OpenAI-compatible HTTP endpoint
> (Groq, OpenAI, Ollama, vLLM, etc.). The `OllamaClient` class name is
> historical — it accepts any provider via the `LLM_BASE_URL` /
> `LLM_API_KEY` / `LLM_MODEL` env vars.

## Imports — canonical paths

```python
# Settings (singleton)
from app.core.config import get_settings, Settings

# Logging
from app.core.logging import configure_logging, get_logger

# Metrics + tracing
from app.core.observability import (
    init_tracing, get_tracer,
    calls_ingested, transcripts_loaded,
    extraction_processed, embeddings_generated, clusters_assigned,
    stage_duration_seconds,
)

# Enums
from app.models.enums import (
    Speaker, Language, Intent, CallStatus,
    QuestionType, FeedbackAction, EdgeRelation,
)

# Schemas
from app.models.schemas import (
    CallMetadata, CallCreate, CallRead,
    UtteranceSchema,
    ExtractedQuestion, ExtractionResult,
    EmbeddingRecord,
    ClusterRecord, ClusterMember, ClusterDetail,
    CanonicalFAQ,
    MemoryEdge, MemoryGraph,
    FeedbackAnnotation,
    SearchRequest, SearchHit, SearchResponse,
    AnalyticsSummary,
)

# Prompts
from app.prompts import (
    EXTRACTION_SYSTEM, EXTRACTION_USER_TEMPLATE,
    CANONICAL_FAQ_SYSTEM, CANONICAL_FAQ_USER_TEMPLATE,
    RELATION_INFERENCE_SYSTEM, RELATION_INFERENCE_USER_TEMPLATE,
)

# Utilities
from app.utils.json_safe import parse_llm_json, LLMJsonError
from app.utils.lang import detect_language, add_e5_prefix
from app.utils.vector import l2_normalize, cosine_sim, cosine_matrix

# LLM client
from app.services.llm.ollama_client import OllamaClient
```

## Service boundaries (function signatures)

### Transcript loader — `app.services.stt.transcript_loader`

```python
def load_transcript(call_id: UUID, json_path: str) -> list[UtteranceSchema]: ...
# Reads a Servo-AI-produced JSON file of {speaker, start_ts, end_ts, text, language?}
# and returns parsed UtteranceSchema objects. The pipeline calls this in place
# of the (deprecated) STT + diarization stages.
```

### Extraction service — `app.services.extraction.llm_extractor`

```python
class LLMExtractor:
    def __init__(self, client: OllamaClient) -> None: ...
    async def extract(self, call_id: UUID, utterances: list[UtteranceSchema]) -> ExtractionResult: ...
```

### Embedding service — `app.services.embedding.e5`

```python
class EmbeddingService:
    async def embed(self, texts: list[str], *, role: str = "passage") -> list[list[float]]: ...
    async def embed_questions(self, questions: list[ExtractedQuestion]) -> list[EmbeddingRecord]: ...
```

### Clustering — `app.clustering.engine`

```python
class ClusterEngine:
    async def assign_incremental(self, embeddings: list[EmbeddingRecord]) -> list[ClusterMember]: ...
    async def rebatch_all(self) -> dict[str, int]: ...
    async def cluster_detail(self, cluster_id: UUID) -> ClusterDetail: ...
```

### Canonicalization — `app.services.canonicalization.faq`

```python
class FAQCanonicalizer:
    def __init__(self, client: OllamaClient, get_cluster_examples_async) -> None: ...
    async def canonicalize(self, cluster_id: UUID) -> CanonicalFAQ: ...
```

### Memory graph — `app.services.memory_graph.builder`

```python
class MemoryGraphBuilder:
    def __init__(self, client: OllamaClient, get_cluster_neighbors_async, list_clusters_async=None) -> None: ...
    async def build_edges_for(self, cluster_id: UUID) -> list[MemoryEdge]: ...
    async def rebuild_global(self) -> int: ...
```

### DB session — `app.db.session`

```python
async def get_session() -> AsyncIterator[AsyncSession]: ...   # FastAPI dependency
def get_sync_session() -> Session: ...                         # for Celery workers
```

## Celery task signatures — `app.workers.tasks`

```python
@celery_app.task(name="v2t.ingest")
def ingest_call(call_id: str) -> None: ...

@celery_app.task(name="v2t._load_transcript")
def load_transcript(call_id: str) -> None:
    """Internal stage replacing the old v2t.stt + v2t.diarize pair."""

@celery_app.task(name="v2t.extract")
def extract_call(call_id: str) -> None: ...

@celery_app.task(name="v2t.embed", queue="gpu.heavy")
def embed_call(call_id: str) -> None: ...

@celery_app.task(name="v2t.cluster")
def cluster_call(call_id: str) -> None: ...

@celery_app.task(name="v2t.canonicalize")
def canonicalize_cluster(cluster_id: str) -> None: ...

@celery_app.task(name="v2t.memory_edges")
def build_memory_edges(cluster_id: str) -> None: ...

@celery_app.task(name="v2t.batch_recluster")
def batch_recluster() -> None: ...   # daily via Celery beat

@celery_app.task(name="v2t.feedback.merge")
def feedback_merge(annotation_id: str) -> None: ...
@celery_app.task(name="v2t.feedback.split")
def feedback_split(annotation_id: str) -> None: ...
@celery_app.task(name="v2t.feedback.relabel")
def feedback_relabel(annotation_id: str) -> None: ...
@celery_app.task(name="v2t.feedback.reassign")
def feedback_reassign(annotation_id: str) -> None: ...
```

## DB tables

`calls(id, source_uri, is_transcript, status, detected_language, duration_seconds,
metadata, created_at, updated_at)`

`utterances(id, call_id, speaker, start_ts, end_ts, text, language, confidence,
words, created_at)`

`extracted_questions(id, call_id, utterance_id, raw_text, normalized_text,
english_gloss, question_type, intent, secondary_intents, language, confidence,
extracted_at)`

`embeddings(id, question_id, model, dim, vector vector(1024), created_at)`

`semantic_clusters(id, label, canonical_question, centroid vector(1024),
dominant_language, dominant_intents, frequency, last_updated, is_stable)`

`cluster_members(cluster_id, question_id PK, similarity, assigned_at)`

`canonical_faqs(id, cluster_id, canonical_question, canonical_question_en,
suggested_answer, language, confidence, version, created_at, updated_at)`

`memory_edges(id, source_cluster_id, target_cluster_id, relation, weight, reason,
created_at)`   *unique (source, target, relation)*

`feedback_annotations(id, action, payload jsonb, author, note, created_at)`

## API routes

- `POST /ingest` — body `CallCreate` (source_uri points to a Servo-AI
  transcript JSON; `is_transcript=true` is implicit) → 202 `{ call_id }`
- `POST /search` — body `SearchRequest` → `SearchResponse`
- `GET  /cluster/{id}` → `ClusterDetail`
- `GET  /faq?intent=&language=&limit=` → `list[CanonicalFAQ]`
- `GET  /memory-graph?min_weight=&limit=` → `MemoryGraph`
- `GET  /analytics` → `AnalyticsSummary`
- `POST /feedback` — body `FeedbackAnnotation` → 202
- `GET  /calls/{id}` → `CallRead`
- `GET  /calls/{id}/utterances` → `list[UtteranceSchema]`
- `GET  /calls/{id}/questions` → `list[ExtractedQuestion]`
- `GET  /healthz` → `{ status, env, version }`
- `GET  /readyz`  → pings DB + Redis
- `GET  /metrics` → Prometheus text exposition
