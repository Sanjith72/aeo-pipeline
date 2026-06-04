"""page_reports — the per-page AEO/SEO report (final deliverable)."""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def put(
    page_id: int,
    run_id: int,
    summary: str | None,
    sections: dict[str, Any],
    *,
    review_status: str = "pending",
) -> int:
    """Insert or refresh a page's report. Keyed on (page_id, run_id)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_reports (page_id, run_id, summary, sections, review_status)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (page_id, run_id) DO UPDATE SET
                summary       = EXCLUDED.summary,
                sections      = EXCLUDED.sections,
                review_status = EXCLUDED.review_status,
                generated_at  = NOW()
            RETURNING id
            """,
            (
                page_id, run_id, summary,
                json.dumps(sections, default=str), review_status,
            ),
        )
        return cur.fetchone()["id"]


def get(page_id: int, run_id: int) -> dict | None:
    """Fetch a page's report, or None if not generated yet."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM page_reports WHERE page_id = %s AND run_id = %s",
            (page_id, run_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def set_review_status(report_id: int, review_status: str) -> None:
    """Flip the Human-Review flag (pending|approved|rejected|could_not_improve)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE page_reports SET review_status = %s WHERE id = %s",
            (review_status, report_id),
        )


def pending_review(run_id: int) -> list[dict]:
    """Reports awaiting Human Review for a run (the review queue)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.page_id, p.url, r.summary, r.review_status, r.generated_at
            FROM page_reports r JOIN crawled_pages p ON p.id = r.page_id
            WHERE r.run_id = %s AND r.review_status IN ('pending','could_not_improve')
            ORDER BY r.generated_at
            """,
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def for_page(page_id: int) -> list[dict]:
    """The most recent report for a single page (0 or 1 rows)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.page_id, r.run_id, p.url, r.summary, r.sections,
                   r.review_status, r.generated_at
            FROM page_reports r JOIN crawled_pages p ON p.id = r.page_id
            WHERE r.page_id = %s
            ORDER BY r.generated_at DESC
            LIMIT 1
            """,
            (page_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def for_run(run_id: int) -> list[dict]:
    """Every page report for a run (full deliverable), worst review state first."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.page_id, r.run_id, p.url, r.summary, r.sections,
                   r.review_status, r.generated_at
            FROM page_reports r JOIN crawled_pages p ON p.id = r.page_id
            WHERE r.run_id = %s
            ORDER BY (r.review_status = 'could_not_improve') DESC,
                     (r.review_status = 'pending') DESC, p.url
            """,
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def for_target(target_id: int, kind: str, *, run_id: int | None = None) -> list[dict]:
    """Page reports owned by a client/competitor target. Defaults to that
    target's most recent run that produced reports."""
    owner_col = "client_id" if kind == "client" else "competitor_id"
    sql = f"""
        SELECT r.id, r.page_id, r.run_id, p.url, r.summary, r.sections,
               r.review_status, r.generated_at
        FROM page_reports r JOIN crawled_pages p ON p.id = r.page_id
        WHERE p.{owner_col} = %(tid)s
          AND r.run_id = COALESCE(
                %(run_id)s,
                (SELECT MAX(r2.run_id)
                 FROM page_reports r2 JOIN crawled_pages p2 ON p2.id = r2.page_id
                 WHERE p2.{owner_col} = %(tid)s)
          )
        ORDER BY (r.review_status = 'could_not_improve') DESC,
                 (r.review_status = 'pending') DESC, p.url
    """
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(sql, {"tid": target_id, "run_id": run_id})
        return [dict(row) for row in cur.fetchall()]
