"""coverage_diffs — site-level Coverage Diff output (one row per run)."""

from __future__ import annotations

import json

from ..db import transaction


def put(
    run_id: int,
    *,
    blueprint_id: int | None,
    target_id: int | None,
    coverage_pct: float,
    missing_count: int,
    thin_count: int,
    detail: dict,
) -> int:
    """Insert or refresh the coverage diff for a run. Keyed on run_id."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coverage_diffs (
                run_id, blueprint_id, target_id, coverage_pct,
                missing_count, thin_count, detail
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                blueprint_id  = EXCLUDED.blueprint_id,
                target_id     = EXCLUDED.target_id,
                coverage_pct  = EXCLUDED.coverage_pct,
                missing_count = EXCLUDED.missing_count,
                thin_count    = EXCLUDED.thin_count,
                detail        = EXCLUDED.detail
                -- created_at is left untouched on refresh: it is the first-insert
                -- time for this run's coverage, not a last-written timestamp.
            RETURNING id
            """,
            (
                run_id, blueprint_id, target_id, coverage_pct,
                missing_count, thin_count, json.dumps(detail, default=str),
            ),
        )
        return cur.fetchone()["id"]


def get(run_id: int) -> dict | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM coverage_diffs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None
