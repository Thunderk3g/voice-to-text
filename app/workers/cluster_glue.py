"""
Sync DB-backed callables required by the (mostly async) clustering and
memory-graph services.

The async `ClusterEngine` / `MemoryGraphBuilder` services in
`app.clustering.engine` and `app.services.memory_graph.builder` need to
read/write Postgres while themselves staying agnostic to the session type.
We hand them the small set of synchronous helpers below; they call them
through a thin protocol (so they can be swapped for fakes in tests).

All helpers expect a `sqlalchemy.orm.Session` from `app.workers.db.sync_session`
and operate only against table names declared in `docs/contracts.md`. No ORM
models are imported here — we rely on SQL/Core to keep this layer thin and
robust to model churn elsewhere.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


# ----------------------------------------------------------------------------
# Cluster reads / writes
# ----------------------------------------------------------------------------
def fetch_active_clusters(session: Session) -> list[dict[str, Any]]:
    """Return every cluster currently considered 'active' (is_stable=True).

    Used by `ClusterEngine.assign_incremental` to find nearest centroid.
    """
    rows = session.execute(
        text(
            """
            SELECT id, label, canonical_question, centroid, dominant_language,
                   dominant_intents, frequency, last_updated, is_stable
            FROM semantic_clusters
            WHERE is_stable = TRUE
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def persist_assignments(
    session: Session,
    assignments: Sequence[dict[str, Any]],
) -> None:
    """Upsert cluster_members rows.

    Each assignment dict must contain: cluster_id, question_id, similarity.
    """
    if not assignments:
        return
    session.execute(
        text(
            """
            INSERT INTO cluster_members (cluster_id, question_id, similarity, assigned_at)
            VALUES (:cluster_id, :question_id, :similarity, NOW())
            ON CONFLICT (question_id) DO UPDATE
            SET cluster_id = EXCLUDED.cluster_id,
                similarity = EXCLUDED.similarity,
                assigned_at = NOW()
            """
        ),
        list(assignments),
    )
    # Bump frequency / last_updated for any touched cluster.
    cluster_ids = {a["cluster_id"] for a in assignments}
    session.execute(
        text(
            """
            UPDATE semantic_clusters
            SET frequency = (
                SELECT COUNT(*) FROM cluster_members WHERE cluster_id = semantic_clusters.id
            ),
            last_updated = NOW()
            WHERE id = ANY(:ids)
            """
        ),
        {"ids": list(cluster_ids)},
    )


def get_cluster_neighbors(
    session: Session,
    cluster_id: UUID | str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Return the top-K nearest clusters to `cluster_id` by cosine distance.

    Uses pgvector's `<=>` (cosine distance) operator on the centroid column.
    """
    rows = session.execute(
        text(
            """
            WITH src AS (
                SELECT centroid FROM semantic_clusters WHERE id = :cid
            )
            SELECT sc.id, sc.label, sc.canonical_question,
                   sc.centroid <=> (SELECT centroid FROM src) AS distance
            FROM semantic_clusters sc, src
            WHERE sc.id <> :cid AND sc.is_stable = TRUE
            ORDER BY sc.centroid <=> (SELECT centroid FROM src) ASC
            LIMIT :k
            """
        ),
        {"cid": str(cluster_id), "k": top_k},
    ).mappings().all()
    return [dict(r) for r in rows]


def get_cluster_examples(
    session: Session,
    cluster_id: UUID | str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return representative extracted questions for a cluster."""
    rows = session.execute(
        text(
            """
            SELECT eq.id, eq.call_id, eq.utterance_id, eq.raw_text,
                   eq.normalized_text, eq.english_gloss, eq.question_type,
                   eq.intent, eq.secondary_intents, eq.language, eq.confidence,
                   eq.extracted_at, cm.similarity
            FROM extracted_questions eq
            JOIN cluster_members cm ON cm.question_id = eq.id
            WHERE cm.cluster_id = :cid
            ORDER BY cm.similarity DESC
            LIMIT :n
            """
        ),
        {"cid": str(cluster_id), "n": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def list_clusters(
    session: Session,
    *,
    only_stable: bool = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List clusters (used by canonicalization sweep, analytics, etc.)."""
    sql = """
        SELECT id, label, canonical_question, centroid, dominant_language,
               dominant_intents, frequency, last_updated, is_stable
        FROM semantic_clusters
    """
    if only_stable:
        sql += " WHERE is_stable = TRUE"
    sql += " ORDER BY frequency DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    rows = session.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


def fetch_cluster_growth(
    session: Session,
    window_days: int = 30,
) -> list[dict[str, Any]]:
    """Daily new-cluster counts over the trailing `window_days`.

    Returns `[{date, new_clusters, churned_clusters}]` rows; churn is
    counted as clusters whose `is_stable` flipped to FALSE inside the day.
    """
    rows = session.execute(
        text(
            """
            WITH days AS (
                SELECT generate_series(
                    date_trunc('day', NOW()) - (:n - 1) * INTERVAL '1 day',
                    date_trunc('day', NOW()),
                    INTERVAL '1 day'
                )::date AS day
            ),
            created AS (
                SELECT date_trunc('day', last_updated)::date AS day, COUNT(*) AS c
                FROM semantic_clusters
                WHERE last_updated >= NOW() - (:n || ' days')::interval
                GROUP BY 1
            ),
            churned AS (
                SELECT date_trunc('day', last_updated)::date AS day, COUNT(*) AS c
                FROM semantic_clusters
                WHERE is_stable = FALSE
                  AND last_updated >= NOW() - (:n || ' days')::interval
                GROUP BY 1
            )
            SELECT d.day AS date,
                   COALESCE(created.c, 0) AS new_clusters,
                   COALESCE(churned.c, 0) AS churned_clusters
            FROM days d
            LEFT JOIN created ON created.day = d.day
            LEFT JOIN churned ON churned.day = d.day
            ORDER BY d.day ASC
            """
        ),
        {"n": window_days},
    ).mappings().all()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# Embedding reads (helpers for clustering)
# ----------------------------------------------------------------------------
def fetch_embeddings_for_call(
    session: Session, call_id: UUID | str
) -> list[dict[str, Any]]:
    """All embeddings for questions belonging to a call."""
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
    return [dict(r) for r in rows]


def fetch_questions_for_call(
    session: Session, call_id: UUID | str
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT * FROM extracted_questions WHERE call_id = :cid"
        ),
        {"cid": str(call_id)},
    ).mappings().all()
    return [dict(r) for r in rows]


def set_call_status(session: Session, call_id: UUID | str, status: str) -> None:
    session.execute(
        text(
            "UPDATE calls SET status = :s, updated_at = NOW() WHERE id = :cid"
        ),
        {"s": status, "cid": str(call_id)},
    )


def insert_utterances(
    session: Session, call_id: UUID | str, utterances: Iterable[dict[str, Any]]
) -> None:
    rows = list(utterances)
    if not rows:
        return
    for r in rows:
        r.setdefault("call_id", str(call_id))
    session.execute(
        text(
            """
            INSERT INTO utterances (call_id, speaker, start_ts, end_ts, text,
                                    language, confidence, words, created_at)
            VALUES (:call_id, :speaker, :start_ts, :end_ts, :text,
                    :language, :confidence, :words, NOW())
            """
        ),
        rows,
    )


__all__ = [
    "fetch_active_clusters",
    "persist_assignments",
    "get_cluster_neighbors",
    "get_cluster_examples",
    "list_clusters",
    "fetch_cluster_growth",
    "fetch_embeddings_for_call",
    "fetch_questions_for_call",
    "set_call_status",
    "insert_utterances",
]
