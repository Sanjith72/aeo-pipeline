"""site_reports — the site-level AEO report (one row per run)."""

from __future__ import annotations

import json

from ...report.site_builder import SiteReport
from ..db import transaction


def put(report: SiteReport) -> int:
    """Upsert the site report for a run. Keyed on run_id."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO site_reports (run_id, target_id, blueprint_id, summary, sections)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                target_id    = EXCLUDED.target_id,
                blueprint_id = EXCLUDED.blueprint_id,
                summary      = EXCLUDED.summary,
                sections     = EXCLUDED.sections
                -- created_at is left untouched on refresh: first-insert time, not
                -- a last-written timestamp.
            RETURNING id
            """,
            (
                report.run_id, report.target_id, report.blueprint_id,
                report.summary, json.dumps(report.sections, default=str),
            ),
        )
        return cur.fetchone()["id"]


def for_run(run_id: int) -> dict | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM site_reports WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None
