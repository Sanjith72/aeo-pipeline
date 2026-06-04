"""agent_traces — per-agent/per-step execution trace (Observability).

Every pipeline stage and the Error Sink call ``record`` once per step. ``for_page``
backs the ``aeo trace <page>`` CLI: a page's full journey, ordered by time.
"""

from __future__ import annotations

from ..db import transaction


def record(
    agent: str,
    status: str,
    *,
    run_id: int | None = None,
    page_id: int | None = None,
    step: str | None = None,
    duration_ms: int | None = None,
    model: str | None = None,
    tokens: int | None = None,
    error: str | None = None,
) -> int:
    """Write one trace row. Returns its id. Never raises on a normal step."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_traces (
                run_id, page_id, agent, step, status,
                duration_ms, model, tokens, error
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, page_id, agent, step, status, duration_ms, model, tokens, error),
        )
        return cur.fetchone()["id"]


def for_page(page_id: int) -> list[dict]:
    """A page's full agent journey, oldest first (for `aeo trace <page>`)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT agent, step, status, duration_ms, model, tokens, error, created_at "
            "FROM agent_traces WHERE page_id = %s ORDER BY created_at, id",
            (page_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def for_run(run_id: int) -> list[dict]:
    """All traces for a run, oldest first (run-level observability)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT page_id, agent, step, status, duration_ms, model, tokens, error, "
            "created_at FROM agent_traces WHERE run_id = %s ORDER BY created_at, id",
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]
