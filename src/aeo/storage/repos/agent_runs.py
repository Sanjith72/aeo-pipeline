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
    dedupes (Postgres treats NULLs as distinct)."""
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
            return dict(row)
        cur.execute("SELECT * FROM agent_runs WHERE idempotency_key = %s", (idempotency_key,))
        return dict(cur.fetchone())


def get(run_id: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def set_status(
    run_id: str,
    status: str,
    *,
    current_step: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    """Advance a run's status. Optional fields are written only when provided, so a
    transition never clobbers an existing result/error with NULL."""
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
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = %s", tuple(params))
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


def list_by_status(status: str, limit: int = 50) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_runs WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
            (status, limit),
        )
        return [dict(row) for row in cur.fetchall()]
