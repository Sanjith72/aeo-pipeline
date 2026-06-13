"""events — product-analytics log + the retention metrics (Block F).

``record`` appends one event; the metric helpers read it back. Arun's headline
metric is **DAU**, but shipping a retention loop with no analytics is flying blind,
so this also computes return-rate, quick-win completion, and — the real "did it
work?" signal — the recommendation **implementation rate**, which joins the
``recommendation_outcomes`` table from the Retention Engine (migration 0010).

Everything lives in Postgres (no third-party analytics), consistent with the rest
of the architecture. The metric SQL needs a live DB, so the round-trips are pinned
in tests/integration/test_db_smoke.py; the offline guards live in
tests/unit/test_events.py.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..db import transaction

# event_type vocabulary the frontend emits (not enforced by the DB — new types are
# free to appear; these are the ones the metrics below understand).
SESSION_START = "session_start"
WIZARD_STEP_COMPLETED = "wizard_step_completed"
PLAN_VIEWED = "plan_viewed"
TASK_MARKED_DONE = "task_marked_done"
RETURN_VISIT = "return_visit"


def record(
    session_id: str,
    event_type: str,
    *,
    client_id: int | None = None,
    url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Append one event. Returns its id. ``metadata`` is stored as JSONB ({} if None)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (session_id, client_id, event_type, url, metadata)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (session_id, client_id, event_type, url, json.dumps(metadata or {}, default=str)),
        )
        return cur.fetchone()["id"]


def dau(start: datetime | None = None, end: datetime | None = None) -> list[dict[str, Any]]:
    """Daily active users — distinct ``session_id`` per calendar day, ascending.

    The optional ``[start, end)`` window bounds the scan (both half-open and
    inclusive of ``start``); omit them for all of history. Returns
    ``[{"day": date, "dau": int}, …]``."""
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("created_at >= %s")
        params.append(start)
    if end is not None:
        clauses.append("created_at < %s")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with transaction() as conn, conn.cursor() as cur:
        # Bucket on the UTC calendar day, not ``created_at::date``: a bare ``::date``
        # cast resolves in the connection's session timezone, so the same data would
        # report different DAU on a UTC host vs. an IST host (and the day boundary
        # would sit at local midnight). Pin it to UTC for a stable, location-independent
        # metric — keep this in sync with return_rate() below.
        cur.execute(
            f"""
            SELECT (created_at AT TIME ZONE 'UTC')::date AS day,
                   COUNT(DISTINCT session_id) AS dau
            FROM events
            {where}
            GROUP BY day
            ORDER BY day
            """,
            tuple(params),
        )
        return [{"day": row["day"], "dau": int(row["dau"])} for row in cur.fetchall()]


def return_rate() -> float:
    """Fraction of sessions that came back — active on **2+ distinct days**. The
    single number closest to "is this retaining anyone?". 0.0 when there are no
    sessions yet (never divides by zero)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH session_days AS (
                -- UTC calendar day, matching dau(): a bare ``created_at::date`` would
                -- count "distinct days" in the session timezone, so a single-day session
                -- whose events straddle local midnight could be miscounted as returning.
                SELECT session_id,
                       COUNT(DISTINCT (created_at AT TIME ZONE 'UTC')::date) AS days
                FROM events
                GROUP BY session_id
            )
            SELECT COALESCE(
                COUNT(*) FILTER (WHERE days >= 2)::float / NULLIF(COUNT(*), 0),
                0.0
            ) AS rate
            FROM session_days
            """
        )
        return float(cur.fetchone()["rate"])


def quick_win_completion_rate() -> float:
    """Of the quick-win tasks the product *presented*, what fraction got *done*.

    Presented ids come from ``plan_viewed`` events (``metadata.quick_win_ids`` — a
    JSON array the results view emits); completions come from ``task_marked_done``
    events flagged ``metadata.quick_win = true``. Counting against presented ids (not
    against all completions) makes this a true completion rate. 0.0 when nothing has
    been presented yet."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            WITH presented AS (
                SELECT DISTINCT jsonb_array_elements_text(metadata->'quick_win_ids') AS task_id
                FROM events
                WHERE event_type = '{PLAN_VIEWED}'
                  AND jsonb_typeof(metadata->'quick_win_ids') = 'array'
            ),
            completed AS (
                SELECT DISTINCT metadata->>'task_id' AS task_id
                FROM events
                WHERE event_type = '{TASK_MARKED_DONE}'
                  AND COALESCE((metadata->>'quick_win')::boolean, false)
            )
            SELECT COALESCE(
                (SELECT COUNT(*) FROM presented p
                 WHERE p.task_id IN (SELECT task_id FROM completed))::float
                / NULLIF((SELECT COUNT(*) FROM presented), 0),
                0.0
            ) AS rate
            """
        )
        return float(cur.fetchone()["rate"])


def recommendation_implementation_rate() -> float:
    """The real "did it work?" signal: of all issued recommendations, the fraction a
    re-crawl detected as **implemented** (Retention Engine, table from 0010). 0.0 when
    no recommendations have been issued yet."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(
                COUNT(*) FILTER (WHERE status = 'implemented')::float / NULLIF(COUNT(*), 0),
                0.0
            ) AS rate
            FROM recommendation_outcomes
            """
        )
        return float(cur.fetchone()["rate"])


def metrics(start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
    """One call for the dashboard: DAU series + the three retention rates."""
    return {
        "dau": dau(start, end),
        "return_rate": return_rate(),
        "quick_win_completion_rate": quick_win_completion_rate(),
        "recommendation_implementation_rate": recommendation_implementation_rate(),
    }
