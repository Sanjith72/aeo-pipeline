"""
DB-backed job queue.

Claim is `SELECT … FOR UPDATE SKIP LOCKED` so multiple workers don't
fight for the same row. No external broker required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ..db import transaction


def enqueue(kind: str, payload: dict[str, Any], run_after: datetime | None = None,
            max_attempts: int = 4) -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (kind, payload, max_attempts, run_after)
            VALUES (%s, %s::jsonb, %s, COALESCE(%s, NOW()))
            RETURNING id
            """,
            (kind, json.dumps(payload, default=str), max_attempts, run_after),
        )
        return cur.fetchone()["id"]


def claim(worker_id: str, kinds: list[str] | None = None) -> dict | None:
    """Atomic claim of the next ready job. Returns the row or None."""
    where_kind = "AND kind = ANY(%s)" if kinds else ""
    params: tuple = (worker_id,)
    if kinds:
        params = (worker_id, kinds)

    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            WITH next AS (
                SELECT id FROM jobs
                WHERE status = 'pending' AND run_after <= NOW()
                {where_kind}
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE jobs j SET
                status = 'running',
                locked_by = %s,
                locked_at = NOW(),
                attempts = attempts + 1,
                updated_at = NOW()
            FROM next WHERE j.id = next.id
            RETURNING j.*
            """,
            params,
        )
        return cur.fetchone()


def succeed(job_id: int) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='succeeded', updated_at=NOW(), locked_by=NULL "
            "WHERE id=%s",
            (job_id,),
        )


def fail(job_id: int, error: str, backoff_sec: int = 30) -> None:
    """
    Mark failed. If attempts < max_attempts, requeue with backoff.
    Otherwise mark 'dead' for manual inspection.
    """
    next_at = datetime.utcnow() + timedelta(seconds=backoff_sec)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                run_after = %s,
                last_error = %s,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (next_at, error, job_id),
        )


def stats() -> dict[str, int]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {row["status"]: row["n"] for row in cur.fetchall()}
