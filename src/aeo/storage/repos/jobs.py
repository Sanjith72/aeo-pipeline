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
    # Placeholders fill in textual order: the kind=ANY(%s) filter (when present) precedes
    # the locked_by=%s assignment, so kinds must come before worker_id in params.
    params: tuple = (kinds, worker_id) if kinds else (worker_id,)

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


def succeed(job_id: int, worker_id: str | None = None) -> bool:
    """Mark done. When ``worker_id`` is given the write is FENCED: it only applies while
    this worker still holds the claim — if the lease expired and the job was reaped (and
    possibly re-claimed by another worker), the late write is discarded. Returns whether
    the write applied."""
    fence = " AND status='running' AND locked_by=%s" if worker_id else ""
    params: tuple = (job_id, worker_id) if worker_id else (job_id,)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='succeeded', updated_at=NOW(), locked_by=NULL "
            f"WHERE id=%s{fence}",
            params,
        )
        return cur.rowcount > 0


def fail(job_id: int, error: str, backoff_sec: int = 30, worker_id: str | None = None) -> bool:
    """
    Mark failed. If attempts < max_attempts, requeue with backoff.
    Otherwise mark 'dead' for manual inspection. Fenced like :func:`succeed`
    when ``worker_id`` is given. Returns whether the write applied.
    """
    fence = " AND status='running' AND locked_by=%s" if worker_id else ""
    next_at = datetime.utcnow() + timedelta(seconds=backoff_sec)
    params: tuple = (next_at, error, job_id) + ((worker_id,) if worker_id else ())
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                run_after = %s,
                last_error = %s,
                locked_by = NULL,
                updated_at = NOW()
            WHERE id = %s{fence}
            """,
            params,
        )
        return cur.rowcount > 0


def reap_stale(lease_sec: int = 1800) -> list[dict[str, Any]]:
    """Recover jobs whose worker vanished mid-run. ``claim`` stamps ``locked_at``; a healthy
    job finishes well inside ``lease_sec``, so anything still 'running' past the lease belongs
    to a dead worker. Requeue while attempts remain (the claim already counted this attempt),
    otherwise mark 'dead'. Returns the reaped rows so callers can fail dependent state
    (e.g. an agent run wedged in 'planning')."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                last_error = %s,
                locked_by = NULL,
                updated_at = NOW()
            WHERE status = 'running' AND locked_at < NOW() - make_interval(secs => %s)
            RETURNING id, kind, payload, status, attempts, max_attempts
            """,
            (f"worker lease expired after {lease_sec}s", lease_sec),
        )
        return [dict(row) for row in cur.fetchall()]


def cancel_pending(kind: str, run_id: str) -> int:
    """Best-effort cancel: kill not-yet-claimed jobs whose payload targets ``run_id`` so no
    worker picks them up. Already-running jobs are left alone — their final writes are
    discarded by the run row's settled-status guard. Returns the number of jobs killed."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'dead', last_error = 'cancelled by user', updated_at = NOW()
            WHERE kind = %s AND status = 'pending' AND payload->>'run_id' = %s
            """,
            (kind, run_id),
        )
        return cur.rowcount


def stats() -> dict[str, int]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {row["status"]: row["n"] for row in cur.fetchall()}
