"""recommendations — append-style log of proposed edits + validation outcome.

A page accumulates rows across criteria and Validation retry attempts. There is
no natural unique key: ``create`` inserts a fresh row per attempt, and the
Validation loop updates status/scores by id via ``set_validation``.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def create(
    page_id: int,
    run_id: int,
    rec_type: str,
    payload: dict[str, Any],
    *,
    criterion: str | None = None,
    attempt: int = 1,
    status: str = "proposed",
    score_before: float | None = None,
) -> int:
    """Insert a proposed recommendation. Returns its id (used by set_validation)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO recommendations (
                page_id, run_id, rec_type, criterion, payload,
                status, attempt, score_before
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            RETURNING id
            """,
            (
                page_id, run_id, rec_type, criterion,
                json.dumps(payload, default=str),
                status, attempt, score_before,
            ),
        )
        return cur.fetchone()["id"]


def set_validation(
    rec_id: int,
    *,
    status: str,
    validated: bool,
    score_after: float | None = None,
) -> None:
    """Record the Validation outcome for a recommendation (re-scored result)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE recommendations
            SET status = %s, validated = %s, score_after = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (status, validated, score_after, rec_id),
        )


def for_page(page_id: int, run_id: int) -> list[dict]:
    """All recommendations for a page/run, oldest first (attempt order)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM recommendations WHERE page_id = %s AND run_id = %s "
            "ORDER BY id",
            (page_id, run_id),
        )
        return [dict(row) for row in cur.fetchall()]
