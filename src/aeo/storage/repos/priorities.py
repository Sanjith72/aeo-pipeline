"""page_priorities — pre-crawl URL ranking (top-N selection per run)."""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def upsert(
    run_id: int,
    url: str,
    page_type: str,
    base_weight: float,
    traffic_signal: float,
    final_score: float,
    *,
    final_rank: int | None = None,
    selected: bool = False,
    detail: dict[str, Any] | None = None,
) -> int:
    """Insert or refresh one URL's priority row. Keyed on (run_id, url)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_priorities (
                run_id, url, page_type, base_weight, traffic_signal,
                final_score, final_rank, selected, detail
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id, url) DO UPDATE SET
                page_type      = EXCLUDED.page_type,
                base_weight    = EXCLUDED.base_weight,
                traffic_signal = EXCLUDED.traffic_signal,
                final_score    = EXCLUDED.final_score,
                final_rank     = EXCLUDED.final_rank,
                selected       = EXCLUDED.selected,
                detail         = EXCLUDED.detail
            RETURNING id
            """,
            (
                run_id, url, page_type, base_weight, traffic_signal,
                final_score, final_rank, selected,
                json.dumps(detail or {}, default=str),
            ),
        )
        return cur.fetchone()["id"]


def selected_urls(run_id: int) -> list[str]:
    """The top-N URLs (selected = TRUE) the per-page pipeline processes, ranked."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT url FROM page_priorities "
            "WHERE run_id = %s AND selected ORDER BY final_rank NULLS LAST, final_score DESC",
            (run_id,),
        )
        return [row["url"] for row in cur.fetchall()]


def ranking(run_id: int) -> list[dict]:
    """Full ranking for a run (observability — including non-selected URLs)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT url, page_type, base_weight, traffic_signal, final_score, "
            "final_rank, selected FROM page_priorities "
            "WHERE run_id = %s ORDER BY final_rank NULLS LAST, final_score DESC",
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]
