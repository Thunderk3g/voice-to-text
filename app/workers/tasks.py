"""
Celery tasks — the canonical pipeline stages.

Every task is a thin orchestrator:
  1. bind structured logging context with the call_id
  2. open a sync session
  3. run the actual (often async) service via `run_async`
  4. update `calls.status` per `app.models.enums.CallStatus`
  5. on transient failure, `self.retry(exc=...)`
  6. on success, hand off to the next stage via `signature(...).apply_async()`

Retry policy
------------
We treat the following as transient and retry with exponential backoff:
  * httpx / openai network errors and 5xx
  * `redis.exceptions.RedisError`
  * SQLAlchemy `OperationalError` (DB blip, deadlock)

Validation errors (`pydantic.ValidationError`, `ValueError`, `KeyError`,
`TypeError`, `LLMJsonError`) are **never** retried — they indicate a code or
data bug and would just burn retries.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterable
from uuid import UUID

import structlog

from app.core.logging import get_logger
from app.core.observability import (
    calls_ingested,
    clusters_assigned,
    embeddings_generated,
    extraction_processed,
    sarvam_transcribed,
    stage_duration_seconds,
    transcripts_loaded,
)
from app.models.enums import CallStatus
from app.workers.celery_app import celery_app
from app.workers.cluster_glue import set_call_status
from app.workers.db import sync_session
from app.workers.run_async import run_async

log = get_logger("v2t.workers.tasks")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
@contextmanager
def _stage(name: str):
    """Time a pipeline stage into the `stage_duration_seconds` histogram."""
    start = time.perf_counter()
    try:
        yield
    finally:
        stage_duration_seconds.labels(stage=name).observe(
            time.perf_counter() - start
        )


def _bind(call_id: str | UUID | None = None, **extra: Any) -> None:
    """Attach contextvars to every log line emitted inside the task."""
    if call_id is not None:
        structlog.contextvars.bind_contextvars(call_id=str(call_id), **extra)
    else:
        structlog.contextvars.bind_contextvars(**extra)


def _transient_exceptions() -> tuple[type[BaseException], ...]:
    """Lazy collection of transient exception types."""
    excs: list[type[BaseException]] = []
    try:
        from sqlalchemy.exc import OperationalError

        excs.append(OperationalError)
    except Exception:  # pragma: no cover
        pass
    try:
        from redis.exceptions import RedisError

        excs.append(RedisError)
    except Exception:  # pragma: no cover
        pass
    try:
        import httpx

        excs.extend([httpx.HTTPError, httpx.TimeoutException])
    except Exception:  # pragma: no cover
        pass
    try:
        from openai import APIConnectionError, APIError, RateLimitError

        excs.extend([APIConnectionError, APIError, RateLimitError])
    except Exception:  # pragma: no cover
        pass
    excs.append(ConnectionError)
    excs.append(TimeoutError)
    return tuple(excs)


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, _transient_exceptions())


def _mark_failed(call_id: str | UUID) -> None:
    try:
        with sync_session() as session:
            set_call_status(session, call_id, CallStatus.FAILED.value)
    except Exception:  # pragma: no cover - best effort
        log.exception("mark_failed_failed", call_id=str(call_id))


def _next(signature_name: str, *args: Any) -> None:
    """Schedule the next pipeline task. Kept tiny so it is trivially mockable."""
    celery_app.signature(signature_name, args=args).apply_async()


# ----------------------------------------------------------------------------
# v2t.ingest — entry point
# ----------------------------------------------------------------------------
@celery_app.task(
    bind=True,
    name="v2t.ingest",
    autoretry_for=(),
    acks_late=True,
)
def ingest_call(self, call_id: str) -> None:
    _bind(call_id, stage="ingest")
    log.info("ingest_start")
    try:
        with _stage("ingest"):
            with sync_session() as session:
                set_call_status(session, call_id, CallStatus.PENDING.value)
            calls_ingested.inc()
        # Pipeline branching: transcript fast-path vs full audio pipeline.
        from app.workers.pipelines import start_call_pipeline

        start_call_pipeline(call_id)
        log.info("ingest_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("ingest_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("ingest_failed")
        _mark_failed(call_id)
        raise


# ----------------------------------------------------------------------------
# v2t._load_transcript — internal stage when audio is already transcribed.
# Used by the transcript fast-path; Sarvam.ai is bypassed.
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t._load_transcript", acks_late=True)
def load_transcript(self, call_id: str) -> None:
    _bind(call_id, stage="load_transcript")
    log.info("load_transcript_start")
    try:
        with _stage("load_transcript"):
            from app.workers.transcript_pipeline import load_transcript_to_utterances

            load_transcript_to_utterances(call_id)
        transcripts_loaded.labels(status="ok").inc()
        _next("v2t.extract", call_id)
        log.info("load_transcript_done")
    except Exception as exc:
        if _is_transient(exc):
            transcripts_loaded.labels(status="retry").inc()
            log.warning("load_transcript_retry", error=str(exc))
            raise self.retry(exc=exc)
        transcripts_loaded.labels(status="error").inc()
        log.exception("load_transcript_failed")
        _mark_failed(call_id)
        raise


# ----------------------------------------------------------------------------
# v2t.transcribe — Sarvam.ai STT + heuristic speaker assignment.
# Used when /ingest receives audio (is_transcript=false).
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.transcribe", acks_late=True)
def transcribe_call(self, call_id: str) -> None:
    _bind(call_id, stage="transcribe")
    log.info("transcribe_start")
    try:
        with _stage("transcribe"):
            with sync_session() as session:
                set_call_status(session, call_id, CallStatus.STT_RUNNING.value)
                row = (
                    session.execute(
                        __import__("sqlalchemy").text(
                            "SELECT source_uri FROM calls WHERE id = :cid"
                        ),
                        {"cid": call_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise ValueError(f"call_id {call_id} not found")
                source_uri = row["source_uri"]

            from app.services.audio.io import cleanup_temp, download_to_temp
            from app.services.stt.sarvam import SarvamTranscriber
            from app.services.stt.speaker_heuristic import assign_speakers

            audio_path = download_to_temp(source_uri)
            try:
                svc = SarvamTranscriber()
                raw_utterances = run_async(
                    svc.transcribe_file(call_id=UUID(call_id), audio_path=audio_path)
                )
            finally:
                cleanup_temp(audio_path)

            utterances = assign_speakers(raw_utterances)

            with sync_session() as session:
                from app.workers.cluster_glue import insert_utterances

                rows = [_utt_dict(call_id, u) for u in utterances]
                insert_utterances(session, call_id, rows)
                set_call_status(session, call_id, CallStatus.DIARIZATION_DONE.value)

        sarvam_transcribed.labels(status="ok").inc()
        _next("v2t.extract", call_id)
        log.info("transcribe_done", n_utterances=len(utterances))
    except Exception as exc:
        if _is_transient(exc):
            sarvam_transcribed.labels(status="retry").inc()
            log.warning("transcribe_retry", error=str(exc))
            raise self.retry(exc=exc)
        sarvam_transcribed.labels(status="error").inc()
        log.exception("transcribe_failed")
        _mark_failed(call_id)
        raise


def _utt_dict(call_id: str, u) -> dict:
    """Convert an UtteranceSchema into the row shape `insert_utterances` expects."""
    if hasattr(u, "model_dump"):
        d = u.model_dump()
    elif isinstance(u, dict):
        d = dict(u)
    else:
        raise TypeError(f"Unsupported utterance type: {type(u)!r}")
    return {
        "call_id": call_id,
        "speaker": str(d.get("speaker", "UNKNOWN")),
        "start_ts": float(d.get("start_ts", 0.0)),
        "end_ts": float(d.get("end_ts", 0.0)),
        "text": d.get("text", ""),
        "language": str(d.get("language", "en")),
        "confidence": float(d.get("confidence", 0.0)),
        "words": d.get("words"),
    }


# ----------------------------------------------------------------------------
# v2t.extract
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.extract", acks_late=True)
def extract_call(self, call_id: str) -> None:
    _bind(call_id, stage="extract")
    log.info("extract_start")
    try:
        with _stage("extract"):
            with sync_session() as session:
                set_call_status(
                    session, call_id, CallStatus.EXTRACTION_RUNNING.value
                )
                utterances = _load_utterances(session, call_id)

            from app.services.factories import make_llm_extractor

            extractor = make_llm_extractor()
            result = run_async(extractor.extract(UUID(str(call_id)), utterances))

            with sync_session() as session:
                _persist_extracted_questions(session, call_id, result.questions)
                set_call_status(
                    session, call_id, CallStatus.EXTRACTION_DONE.value
                )
        extraction_processed.labels(status="ok").inc()
        _next("v2t.embed", call_id)
        log.info("extract_done", n_questions=len(result.questions))
    except Exception as exc:
        if _is_transient(exc):
            extraction_processed.labels(status="retry").inc()
            log.warning("extract_retry", error=str(exc))
            raise self.retry(exc=exc)
        extraction_processed.labels(status="error").inc()
        log.exception("extract_failed")
        _mark_failed(call_id)
        raise


# ----------------------------------------------------------------------------
# v2t.embed
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.embed", queue="gpu.heavy", acks_late=True)
def embed_call(self, call_id: str) -> None:
    _bind(call_id, stage="embed")
    log.info("embed_start")
    try:
        with _stage("embed"):
            with sync_session() as session:
                questions = _load_questions(session, call_id)

            from app.services.factories import get_embedding_service

            svc = get_embedding_service()
            embeddings = run_async(svc.embed_questions(questions))

            with sync_session() as session:
                _persist_embeddings(session, embeddings)
                set_call_status(
                    session, call_id, CallStatus.EMBEDDING_DONE.value
                )
        embeddings_generated.inc(len(embeddings))
        _next("v2t.cluster", call_id)
        log.info("embed_done", n_embeddings=len(embeddings))
    except Exception as exc:
        if _is_transient(exc):
            log.warning("embed_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("embed_failed")
        _mark_failed(call_id)
        raise


# ----------------------------------------------------------------------------
# v2t.cluster
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.cluster", acks_late=True)
def cluster_call(self, call_id: str) -> None:
    _bind(call_id, stage="cluster")
    log.info("cluster_start")
    try:
        with _stage("cluster"):
            with sync_session() as session:
                embeddings = _load_embeddings(session, call_id)

            from app.services.factories import make_cluster_engine

            engine = make_cluster_engine()
            members = run_async(engine.assign_incremental(embeddings))

            with sync_session() as session:
                from app.workers.cluster_glue import persist_assignments

                persist_assignments(
                    session,
                    [
                        {
                            "cluster_id": str(m.cluster_id),
                            "question_id": str(m.question_id),
                            "similarity": float(m.similarity),
                        }
                        for m in members
                    ],
                )
                set_call_status(session, call_id, CallStatus.CLUSTERED.value)
        clusters_assigned.labels(mode="incremental").inc(len(members))
        # Fan out canonicalization + memory edge construction per affected cluster.
        for cid in {str(m.cluster_id) for m in members}:
            _next("v2t.canonicalize", cid)
            _next("v2t.memory_edges", cid)
        log.info("cluster_done", n_members=len(members))
    except Exception as exc:
        if _is_transient(exc):
            log.warning("cluster_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("cluster_failed")
        _mark_failed(call_id)
        raise


# ----------------------------------------------------------------------------
# v2t.canonicalize
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.canonicalize", acks_late=True)
def canonicalize_cluster(self, cluster_id: str) -> None:
    _bind(cluster_id=cluster_id, stage="canonicalize")
    log.info("canonicalize_start")
    try:
        with _stage("canonicalize"):
            from app.services.factories import make_faq_canonicalizer

            canon = make_faq_canonicalizer()
            faq = run_async(canon.canonicalize(UUID(cluster_id)))

            with sync_session() as session:
                _persist_canonical_faq(session, faq)
        log.info("canonicalize_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("canonicalize_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("canonicalize_failed")
        raise


# ----------------------------------------------------------------------------
# v2t.memory_edges
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.memory_edges", acks_late=True)
def build_memory_edges(self, cluster_id: str) -> None:
    _bind(cluster_id=cluster_id, stage="memory_edges")
    log.info("memory_edges_start")
    try:
        with _stage("memory_edges"):
            from app.services.factories import make_memory_graph_builder

            builder = make_memory_graph_builder()
            edges = run_async(builder.build_edges_for(UUID(cluster_id)))

            with sync_session() as session:
                _persist_memory_edges(session, edges)
        log.info("memory_edges_done", n_edges=len(edges))
    except Exception as exc:
        if _is_transient(exc):
            log.warning("memory_edges_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("memory_edges_failed")
        raise


# ----------------------------------------------------------------------------
# v2t.batch_recluster — beat-scheduled
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.batch_recluster", acks_late=True)
def batch_recluster(self) -> None:
    _bind(stage="batch_recluster")
    log.info("batch_recluster_start")
    try:
        with _stage("batch_recluster"):
            from app.services.factories import make_cluster_engine

            engine = make_cluster_engine()
            counts = run_async(engine.rebatch_all())
        clusters_assigned.labels(mode="batch").inc(int(counts.get("updated", 0)))
        log.info("batch_recluster_done", **counts)
    except Exception as exc:
        if _is_transient(exc):
            log.warning("batch_recluster_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("batch_recluster_failed")
        raise


# ----------------------------------------------------------------------------
# Human feedback tasks
# ----------------------------------------------------------------------------
@celery_app.task(bind=True, name="v2t.feedback.merge", acks_late=True)
def feedback_merge(self, payload: dict[str, Any]) -> None:
    """Merge cluster A into B: rewrite cluster_members, deactivate A."""
    _bind(stage="feedback.merge", source=payload.get("source_id"))
    log.info("feedback_merge_start", payload=payload)
    try:
        with _stage("feedback.merge"):
            src = payload["source_id"]
            tgt = payload["target_id"]
            with sync_session() as session:
                from sqlalchemy import text

                session.execute(
                    text(
                        "UPDATE cluster_members SET cluster_id = :tgt WHERE cluster_id = :src"
                    ),
                    {"src": src, "tgt": tgt},
                )
                session.execute(
                    text(
                        "UPDATE semantic_clusters SET is_stable = FALSE, last_updated = NOW() WHERE id = :src"
                    ),
                    {"src": src},
                )
        _next("v2t.canonicalize", tgt)
        log.info("feedback_merge_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("feedback_merge_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("feedback_merge_failed")
        raise


@celery_app.task(bind=True, name="v2t.feedback.split", acks_late=True)
def feedback_split(self, payload: dict[str, Any]) -> None:
    """Rerun HDBSCAN on members of one cluster."""
    _bind(stage="feedback.split", cluster_id=payload.get("cluster_id"))
    log.info("feedback_split_start", payload=payload)
    try:
        with _stage("feedback.split"):
            from app.clustering.engine import ClusterEngine

            engine = ClusterEngine()
            run_async(engine.rebatch_all())  # service handles single-cluster split
        log.info("feedback_split_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("feedback_split_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("feedback_split_failed")
        raise


@celery_app.task(bind=True, name="v2t.feedback.relabel", acks_late=True)
def feedback_relabel(self, payload: dict[str, Any]) -> None:
    """Bulk update intent / label for a cluster's questions."""
    _bind(stage="feedback.relabel", cluster_id=payload.get("cluster_id"))
    log.info("feedback_relabel_start", payload=payload)
    try:
        with _stage("feedback.relabel"):
            cluster_id = payload["cluster_id"]
            new_intent = payload.get("intent")
            new_label = payload.get("label")
            with sync_session() as session:
                from sqlalchemy import text

                if new_intent is not None:
                    session.execute(
                        text(
                            """
                            UPDATE extracted_questions
                            SET intent = :i
                            WHERE id IN (
                                SELECT question_id FROM cluster_members WHERE cluster_id = :c
                            )
                            """
                        ),
                        {"i": new_intent, "c": cluster_id},
                    )
                if new_label is not None:
                    session.execute(
                        text(
                            "UPDATE semantic_clusters SET label = :l, last_updated = NOW() WHERE id = :c"
                        ),
                        {"l": new_label, "c": cluster_id},
                    )
        log.info("feedback_relabel_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("feedback_relabel_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("feedback_relabel_failed")
        raise


@celery_app.task(bind=True, name="v2t.feedback.reassign", acks_late=True)
def feedback_reassign(self, payload: dict[str, Any]) -> None:
    """Move a single question from one cluster to another."""
    _bind(stage="feedback.reassign", question_id=payload.get("question_id"))
    log.info("feedback_reassign_start", payload=payload)
    try:
        with _stage("feedback.reassign"):
            qid = payload["question_id"]
            new_cluster = payload["cluster_id"]
            with sync_session() as session:
                from sqlalchemy import text

                session.execute(
                    text(
                        """
                        INSERT INTO cluster_members (cluster_id, question_id, similarity, assigned_at)
                        VALUES (:c, :q, :s, NOW())
                        ON CONFLICT (question_id) DO UPDATE
                        SET cluster_id = EXCLUDED.cluster_id,
                            similarity = EXCLUDED.similarity,
                            assigned_at = NOW()
                        """
                    ),
                    {
                        "c": new_cluster,
                        "q": qid,
                        "s": float(payload.get("similarity", 1.0)),
                    },
                )
        log.info("feedback_reassign_done")
    except Exception as exc:
        if _is_transient(exc):
            log.warning("feedback_reassign_retry", error=str(exc))
            raise self.retry(exc=exc)
        log.exception("feedback_reassign_failed")
        raise


# ----------------------------------------------------------------------------
# Small persistence shims (kept local to the worker to avoid model coupling)
# ----------------------------------------------------------------------------
def _get_source_uri(session, call_id: str | UUID) -> str:
    from sqlalchemy import text

    row = session.execute(
        text("SELECT source_uri FROM calls WHERE id = :cid"),
        {"cid": str(call_id)},
    ).mappings().first()
    if row is None:
        raise ValueError(f"call_id {call_id} not found")
    return str(row["source_uri"])


def _utt_to_row(call_id: str, u: Any) -> dict[str, Any]:
    if hasattr(u, "model_dump"):
        d = u.model_dump()
    elif isinstance(u, dict):
        d = dict(u)
    else:
        raise TypeError(f"Unsupported utterance type: {type(u)!r}")
    return {
        "call_id": str(call_id),
        "speaker": str(d.get("speaker", "UNKNOWN")),
        "start_ts": float(d.get("start_ts", 0.0)),
        "end_ts": float(d.get("end_ts", 0.0)),
        "text": d.get("text", ""),
        "language": str(d.get("language", "en")),
        "confidence": float(d.get("confidence", 0.0)),
        "words": d.get("words"),
    }


def _load_utterances(session, call_id: str | UUID) -> list[Any]:
    from sqlalchemy import text

    from app.models.schemas import UtteranceSchema

    rows = session.execute(
        text(
            """
            SELECT id, call_id, speaker, start_ts, end_ts, text, language,
                   confidence, words
            FROM utterances WHERE call_id = :cid ORDER BY start_ts ASC
            """
        ),
        {"cid": str(call_id)},
    ).mappings().all()
    out: list[Any] = []
    for r in rows:
        out.append(UtteranceSchema.model_validate(dict(r)))
    return out


def _update_utterance_speakers(session, utterances: Iterable[Any]) -> None:
    from sqlalchemy import text

    for u in utterances:
        d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
        if not d.get("id"):
            continue
        session.execute(
            text(
                "UPDATE utterances SET speaker = :sp WHERE id = :id"
            ),
            {"sp": str(d.get("speaker", "UNKNOWN")), "id": str(d["id"])},
        )


def _load_questions(session, call_id: str | UUID) -> list[Any]:
    from sqlalchemy import text

    from app.models.schemas import ExtractedQuestion

    rows = session.execute(
        text("SELECT * FROM extracted_questions WHERE call_id = :cid"),
        {"cid": str(call_id)},
    ).mappings().all()
    return [ExtractedQuestion.model_validate(dict(r)) for r in rows]


def _load_embeddings(session, call_id: str | UUID) -> list[Any]:
    from sqlalchemy import text

    from app.models.schemas import EmbeddingRecord

    rows = session.execute(
        text(
            """
            SELECT e.id, e.question_id, e.model, e.dim, e.vector, e.created_at
            FROM embeddings e
            JOIN extracted_questions q ON q.id = e.question_id
            WHERE q.call_id = :cid
            """
        ),
        {"cid": str(call_id)},
    ).mappings().all()
    out: list[Any] = []
    for r in rows:
        d = dict(r)
        # pgvector may come back as list-like or string; normalize to list[float].
        vec = d.get("vector")
        if isinstance(vec, str):
            vec = [float(x) for x in vec.strip("[]").split(",") if x]
        d["vector"] = list(vec) if vec is not None else []
        out.append(EmbeddingRecord.model_validate(d))
    return out


def _persist_extracted_questions(
    session, call_id: str | UUID, questions: Iterable[Any]
) -> None:
    from sqlalchemy import text

    rows = []
    for q in questions:
        d = q.model_dump() if hasattr(q, "model_dump") else dict(q)
        rows.append(
            {
                "call_id": str(call_id),
                "utterance_id": str(d["utterance_id"]) if d.get("utterance_id") else None,
                "raw_text": d.get("raw_text", ""),
                "normalized_text": d.get("normalized_text", ""),
                "english_gloss": d.get("english_gloss"),
                "question_type": str(d.get("question_type", "question")),
                "intent": str(d.get("intent", "other")),
                "secondary_intents": [str(i) for i in d.get("secondary_intents", [])],
                "language": str(d.get("language", "en")),
                "confidence": float(d.get("confidence", 0.0)),
            }
        )
    if not rows:
        return
    session.execute(
        text(
            """
            INSERT INTO extracted_questions
                (call_id, utterance_id, raw_text, normalized_text, english_gloss,
                 question_type, intent, secondary_intents, language, confidence,
                 extracted_at)
            VALUES
                (:call_id, :utterance_id, :raw_text, :normalized_text, :english_gloss,
                 :question_type, :intent, :secondary_intents, :language, :confidence,
                 NOW())
            """
        ),
        rows,
    )


def _persist_embeddings(session, embeddings: Iterable[Any]) -> None:
    from sqlalchemy import text

    rows = []
    for e in embeddings:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        rows.append(
            {
                "question_id": str(d["question_id"]),
                "model": d.get("model", ""),
                "dim": int(d.get("dim", 0)),
                "vector": list(d.get("vector", [])),
            }
        )
    if not rows:
        return
    session.execute(
        text(
            """
            INSERT INTO embeddings (question_id, model, dim, vector, created_at)
            VALUES (:question_id, :model, :dim, :vector, NOW())
            """
        ),
        rows,
    )


def _persist_canonical_faq(session, faq: Any) -> None:
    from sqlalchemy import text

    d = faq.model_dump() if hasattr(faq, "model_dump") else dict(faq)
    session.execute(
        text(
            """
            INSERT INTO canonical_faqs
                (cluster_id, canonical_question, canonical_question_en,
                 suggested_answer, language, confidence, version, created_at,
                 updated_at)
            VALUES
                (:cluster_id, :canonical_question, :canonical_question_en,
                 :suggested_answer, :language, :confidence, :version, NOW(), NOW())
            """
        ),
        {
            "cluster_id": str(d["cluster_id"]),
            "canonical_question": d.get("canonical_question", ""),
            "canonical_question_en": d.get("canonical_question_en"),
            "suggested_answer": d.get("suggested_answer"),
            "language": str(d.get("language", "en")),
            "confidence": float(d.get("confidence", 0.0)),
            "version": int(d.get("version", 1)),
        },
    )


def _persist_memory_edges(session, edges: Iterable[Any]) -> None:
    from sqlalchemy import text

    rows = []
    for e in edges:
        d = e.model_dump() if hasattr(e, "model_dump") else dict(e)
        rows.append(
            {
                "source_cluster_id": str(d["source_cluster_id"]),
                "target_cluster_id": str(d["target_cluster_id"]),
                "relation": str(d["relation"]),
                "weight": float(d.get("weight", 0.0)),
                "reason": d.get("reason"),
            }
        )
    if not rows:
        return
    session.execute(
        text(
            """
            INSERT INTO memory_edges
                (source_cluster_id, target_cluster_id, relation, weight, reason, created_at)
            VALUES
                (:source_cluster_id, :target_cluster_id, :relation, :weight, :reason, NOW())
            ON CONFLICT (source_cluster_id, target_cluster_id, relation) DO UPDATE
            SET weight = EXCLUDED.weight,
                reason = EXCLUDED.reason
            """
        ),
        rows,
    )


__all__ = [
    "ingest_call",
    "stt_call",
    "diarize_call",
    "extract_call",
    "embed_call",
    "cluster_call",
    "canonicalize_cluster",
    "build_memory_edges",
    "batch_recluster",
    "feedback_merge",
    "feedback_split",
    "feedback_relabel",
    "feedback_reassign",
]
