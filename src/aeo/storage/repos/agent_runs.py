"""agent_runs / agent_steps — durable state for the Phase 2 agent runtime.

A run is the resumable artifact behind one assistive-copilot pass: the Planner stages a
task graph, a human approves/rejects it. Identity is a minted token (:func:`new_id`).
``idempotency_key`` (optional, UNIQUE) collapses duplicate enqueues. Like the other repos,
every function only touches the DB at call time via ``transaction()``.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from ..db import transaction

_TOKEN_BYTES = 9  # ~12 url-safe chars, matches plan_state ids


def new_id() -> str:
    """A fresh, unguessable agent-run id."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def create(
    *,
    idempotency_key: str | None = None,
    domain: str | None = None,
    client_id: int | None = None,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new run (status 'queued') and return its row. When ``idempotency_key`` is
    set and already exists, return the existing run instead (dedupe). A NULL key never
    dedupes (Postgres treats NULLs as distinct). The returned dict carries a transient
    ``_inserted`` flag (True = this call created the row) so the caller can decide whether
    to enqueue work — a deduped replay must not get a second job."""
    rid = new_id()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_runs (id, idempotency_key, domain, client_id, brief)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (rid, idempotency_key, domain, client_id, json.dumps(brief or {}, default=str)),
        )
        row = cur.fetchone()
        if row is not None:
            return {**dict(row), "_inserted": True}
        cur.execute("SELECT * FROM agent_runs WHERE idempotency_key = %s", (idempotency_key,))
        return {**dict(cur.fetchone()), "_inserted": False}


def get(run_id: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def by_idempotency_key(key: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_runs WHERE idempotency_key = %s", (key,))
        row = cur.fetchone()
        return dict(row) if row else None


# Once a run settles nothing may move it again: a worker finishing after a user's cancel
# (or a reaped duplicate delivery) must not resurrect the run. 'staged' is deliberately
# NOT settled — the human approve/reject transition starts there.
_SETTLED = ("approved", "rejected", "failed", "cancelled")


def set_status(
    run_id: str,
    status: str,
    *,
    current_step: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    only_from: tuple[str, ...] | None = None,
) -> bool:
    """Advance a run's status. Optional fields are written only when provided, so a
    transition never clobbers an existing result/error with NULL. Returns False when the
    run is unknown or already settled (see ``_SETTLED``) — settled runs are immutable.
    ``only_from`` makes the transition a compare-and-set: it applies only while the run is
    in one of those statuses, so racing writers (approve vs reject vs cancel vs a late
    worker) cannot both report success."""
    sets = ["status = %s"]
    params: list[Any] = [status]
    if current_step is not None:
        sets.append("current_step = %s")
        params.append(current_step)
    if result is not None:
        sets.append("result = %s::jsonb")
        params.append(json.dumps(result, default=str))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    params.append(run_id)
    params.append(list(_SETTLED))
    where = "id = %s AND NOT (status = ANY(%s))"
    if only_from is not None:
        where += " AND status = ANY(%s)"
        params.append(list(only_from))
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE {where}", tuple(params))
        return cur.rowcount > 0


def max_seq(run_id: str) -> int:
    """Highest persisted step seq for a run (0 when none). The controller resumes
    numbering from here on at-least-once redelivery, so a retried run never collides
    with the abandoned attempt's rows (UNIQUE(run_id, seq))."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM agent_steps WHERE run_id = %s", (run_id,))
        return int(cur.fetchone()["m"])


def append_step(
    run_id: str,
    *,
    seq: int,
    agent: str,
    tool: str | None = None,
    status: str = "ok",
    model: str | None = None,
    tokens: int | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    error_class: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_steps
                (run_id, seq, agent, tool, status, model, tokens, cost_usd, latency_ms, error_class, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (run_id, seq, agent, tool, status, model, tokens, cost_usd, latency_ms,
             error_class, json.dumps(detail or {}, default=str)),
        )
        return cur.fetchone()["id"]


def steps_for(run_id: str) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_steps WHERE run_id = %s ORDER BY seq", (run_id,))
        return [dict(row) for row in cur.fetchall()]


def list_by_status(status: str | list[str], limit: int = 50) -> list[dict[str, Any]]:
    """Runs in the given status (or any of a list of statuses), newest first."""
    statuses = [status] if isinstance(status, str) else list(status)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_runs WHERE status = ANY(%s) ORDER BY updated_at DESC LIMIT %s",
            (statuses, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def count_active() -> int:
    """Runs currently in flight (queued or planning) — the enqueue cap reads this."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM agent_runs WHERE status IN ('queued', 'planning')")
        return int(cur.fetchone()["n"])
