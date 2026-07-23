"""packs — v5 CH-03 pack persistence. Header rows in `packs`; pack membership rides
the existing per-run ranking table (`page_priorities.pack_index`, migration 0028), so
no page JSON is duplicated. Only the SELECTED top-N (+ the always-admitted homepage) —
what the audit actually crawls/scores — receive a pack_index; the rest stay NULL.

`locked` is NOT stored here — it is entitlement-derived at the API layer
(aeo.entitlements.logic.decorate_pack); this repo is entitlement-agnostic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..db import transaction

if TYPE_CHECKING:
    from ...pipeline.packs import Pack


def put_for_run(run_id: int, packs: list[Pack]) -> int:
    """Persist pack headers + back-fill page_priorities.pack_index in ONE transaction.
    Idempotent per run (a re-persist clears stale membership + headers first, so a run
    that yields fewer packs never orphans higher-index rows). Must run AFTER
    persist_ranking — the back-fill UPDATEs the page_priorities rows it committed."""
    with transaction() as conn, conn.cursor() as cur:
        # Clear stale membership + headers first so a re-persist is clean.
        cur.execute("UPDATE page_priorities SET pack_index = NULL WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM packs WHERE run_id = %s", (run_id,))
        for pack in packs:
            # status omitted → the DDL default 'preview' applies (the seam runs before the
            # crawl, so nothing is scored yet; status is pipeline-progress, never the lock).
            cur.execute(
                """
                INSERT INTO packs (run_id, pack_index, title, impact_score, page_count)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (run_id, pack.pack_index, pack.title, pack.impact_score, len(pack.pages)),
            )
            for page in pack.pages:
                cur.execute(
                    "UPDATE page_priorities SET pack_index = %s WHERE run_id = %s AND url = %s",
                    (pack.pack_index, run_id, page.url),
                )
    return len(packs)


def by_run(run_id: int) -> list[dict[str, Any]]:
    """Reconstruct the §b pack objects for a run from the headers + page_priorities.
    Pages carry {url, page_type, final_score, rank}. No `locked` — that is derived at
    the API layer from entitlements. Empty list when the run has no persisted packs."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pack_index, title, impact_score, page_count, status "
            "FROM packs WHERE run_id = %s ORDER BY pack_index",
            (run_id,),
        )
        headers = [dict(row) for row in cur.fetchall()]
        if not headers:
            return []
        cur.execute(
            # Homepage first within its pack, then by rank — reproducing build_packs'
            # forced homepage-first ordering of Pack 1 (only Pack 1 ever holds the
            # homepage), so the persisted view and the live overview agree on page order.
            "SELECT url, page_type, final_score, final_rank, pack_index "
            "FROM page_priorities WHERE run_id = %s AND pack_index IS NOT NULL "
            "ORDER BY pack_index, (page_type = 'homepage') DESC, final_rank NULLS LAST, final_score DESC",
            (run_id,),
        )
        pages_by_pack: dict[int, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            pages_by_pack.setdefault(row["pack_index"], []).append(
                {
                    "url": row["url"],
                    "page_type": row["page_type"],
                    "final_score": float(row["final_score"]) if row["final_score"] is not None else 0.0,
                    "rank": row["final_rank"],
                }
            )
    return [
        {
            "pack_index": h["pack_index"],
            "title": h["title"],
            "impact_score": float(h["impact_score"]) if h["impact_score"] is not None else 0.0,
            "page_count": h["page_count"],
            "status": h["status"],
            "pages": pages_by_pack.get(h["pack_index"], []),
        }
        for h in headers
    ]


def by_domain(domain: str) -> list[dict[str, Any]]:
    """The latest run's packs for a domain (crawl_runs has no domain column — reuse the
    host join in runs.latest_for_domain). Empty when the domain has no scored run."""
    from . import runs as runs_repo

    latest = runs_repo.latest_for_domain(domain)
    return by_run(latest["run_id"]) if latest and latest.get("run_id") else []
