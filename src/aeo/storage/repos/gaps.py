"""gap_analyses — Dual-Layer Gap Analysis output (one row per page/run)."""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def put(
    page_id: int,
    run_id: int,
    bestpractice_gap: float,
    competitor_gap: float,
    overall_gap: float,
    detail: dict[str, Any] | None = None,
) -> int:
    """Insert or refresh the gap analysis for a page. Keyed on (page_id, run_id)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gap_analyses (
                page_id, run_id, bestpractice_gap, competitor_gap, overall_gap, detail
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (page_id, run_id) DO UPDATE SET
                bestpractice_gap = EXCLUDED.bestpractice_gap,
                competitor_gap   = EXCLUDED.competitor_gap,
                overall_gap      = EXCLUDED.overall_gap,
                detail           = EXCLUDED.detail,
                created_at       = NOW()
            RETURNING id
            """,
            (
                page_id, run_id, bestpractice_gap, competitor_gap, overall_gap,
                json.dumps(detail or {}, default=str),
            ),
        )
        return cur.fetchone()["id"]


def get(page_id: int, run_id: int) -> dict | None:
    """Fetch the gap analysis for a page/run, or None if not computed yet."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM gap_analyses WHERE page_id = %s AND run_id = %s",
            (page_id, run_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None
