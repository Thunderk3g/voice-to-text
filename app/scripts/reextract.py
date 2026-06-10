"""Re-enqueue extraction for already-processed calls.

Default is a DRY RUN that only prints what would happen. With ``--apply``:
  1. deletes extracted_questions for the selected calls (cascades to
     embeddings + cluster_members),
  2. prunes semantic_clusters left with zero members (and their
     canonical_faqs / memory_edges via FK cascade),
  3. resets call status to diarization_done,
  4. enqueues v2t.extract per call.

Usage::

    python -m app.scripts.reextract                  # dry run
    python -m app.scripts.reextract --apply
    python -m app.scripts.reextract --apply --statuses clustered,extraction_done
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.workers.celery_app import celery_app
from app.workers.db import sync_session

logger = get_logger(__name__)

DEFAULT_STATUSES = "clustered,extraction_done,embedding_done"


def run(statuses: list[str], apply: bool) -> int:
    with sync_session() as session:
        calls = session.execute(
            text(
                "SELECT id, status FROM calls"
                " WHERE status::text = ANY(:s) ORDER BY created_at"
            ),
            {"s": statuses},
        ).mappings().all()
        ids = [str(c["id"]) for c in calls]
        n_questions = session.execute(
            text(
                "SELECT COUNT(*) FROM extracted_questions"
                " WHERE call_id = ANY(:ids)"
            ),
            {"ids": ids},
        ).scalar_one() if ids else 0

        print(f"Selected {len(calls)} calls (statuses={statuses}); "
              f"{n_questions} extracted_questions would be purged.")
        if not calls:
            return 0
        if not apply:
            print("DRY RUN — re-run with --apply to execute.")
            return 0

        session.execute(
            text("DELETE FROM extracted_questions WHERE call_id = ANY(:ids)"),
            {"ids": ids},
        )
        pruned = session.execute(
            text(
                """
                DELETE FROM semantic_clusters
                WHERE id NOT IN (SELECT DISTINCT cluster_id FROM cluster_members)
                RETURNING id
                """
            )
        ).fetchall()
        session.execute(
            text(
                "UPDATE calls SET status = 'diarization_done', updated_at = NOW()"
                " WHERE id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        print(f"Purged questions for {len(ids)} calls; pruned {len(pruned)} empty clusters.")

    for cid in ids:
        celery_app.send_task("v2t.extract", args=[cid])
    print(f"Enqueued v2t.extract for {len(ids)} calls.")
    return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="app.scripts.reextract")
    parser.add_argument("--apply", action="store_true", help="Execute (default: dry run).")
    parser.add_argument("--statuses", default=DEFAULT_STATUSES)
    args = parser.parse_args()
    return run([s.strip() for s in args.statuses.split(",") if s.strip()], args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
