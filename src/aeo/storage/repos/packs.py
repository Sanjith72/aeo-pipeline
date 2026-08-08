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


def completed_pack_indices(run_id: int) -> set[int]:
    """The pack indices whose v5 tickets are ALL verified_completed (v5 CH-15) — the input
    to the progressive-unlock resolver (is_pack_locked's completed_pack_indices). A pack
    counts only when it has ≥1 ticket AND every ticket is verified (so a pack with no
    tickets never unlocks the next one for free). Domain-scoped: resolves the run's domain
    → client → its 'pack:N' milestones, so verification by a newer re-crawl run still
    counts. Empty set when the domain has no client/tickets (Pack-1-only preserved)."""
    from . import runs as runs_repo
    from . import targets as targets_repo

    domain = runs_repo.domain_for_run(run_id)
    if not domain:
        return set()
    target = targets_repo.by_domain(domain)
    if target is None:
        return set()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT CAST(SUBSTRING(m.milestone_key FROM 6) AS INTEGER) AS pack_index,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE t.status = 'verified_completed') AS verified
              FROM implementation_milestones m
              JOIN milestone_tasks t ON t.milestone_id = m.id
             WHERE m.client_id = %s AND m.milestone_key LIKE 'pack:%%'
             GROUP BY m.milestone_key
            """,
            (target.id,),
        )
        return {
            r["pack_index"] for r in cur.fetchall()
            if r["total"] > 0 and r["verified"] == r["total"]
        }


def by_domain(domain: str) -> list[dict[str, Any]]:
    """The latest run's packs for a domain (crawl_runs has no domain column — reuse the
    host join in runs.latest_for_domain). Empty when the domain has no scored run."""
    from . import runs as runs_repo

    latest = runs_repo.latest_for_domain(domain)
    return by_run(latest["run_id"]) if latest and latest.get("run_id") else []


def latest_pack_run_for_domain(domain: str) -> int | None:
    """The newest run for EXACTLY this host that has persisted packs — the run a saved
    plan can actually resume onto (packs → page scores → tickets).

    Two deliberate tightenings over ``runs.latest_for_domain``, both load-bearing:

    * **Exact host, never a prefix.** ``normalize`` guarantees the host is followed by
      ``/`` or ``:`` in ``url_normalized``, so matching ``host/`` and ``host:`` means
      ``acme.co`` can never match ``acme.com``'s pages. latest_for_domain's bare
      ``LIKE 'https://host%%'`` does exactly that, and its newest-wins ordering would
      then bind ANOTHER site's run — packs, scores, ticket board — to this plan.
    * **Packs must exist.** A cancelled/failed re-crawl or a ticket-verify crawl keeps
      crawled pages but persists no packs; picking it would resume the plan onto a run
      that cannot serve the Pages tab while an older, fully scored run sits unused.

    Both ``www.`` spellings are tried (grants and plans key on the bare domain; crawls
    keep the site's own spelling). LIKE metacharacters in the host are escaped — the
    domain here comes from a user-supplied plan row, not from our own crawler.
    """
    from ...reference.domain_config import normalize_domain

    bare = normalize_domain(domain)
    if not bare:
        return None
    hosts = {bare, f"www.{bare}"}
    patterns: list[str] = []
    for h in hosts:
        esc = h.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        patterns.extend(
            f"{scheme}://{esc}{sep}%" for scheme in ("https", "http") for sep in ("/", ":")
        )
    where = " OR ".join(["cp.url_normalized LIKE %s ESCAPE '\\'"] * len(patterns))
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT cp.run_id AS run_id, MAX(cp.crawled_at) AS last_crawled_at
              FROM crawled_pages cp
             WHERE ({where})
               AND EXISTS (SELECT 1 FROM packs p WHERE p.run_id = cp.run_id)
             GROUP BY cp.run_id
             ORDER BY last_crawled_at DESC
             LIMIT 1
            """,
            patterns,
        )
        row = cur.fetchone()
    return row["run_id"] if row else None
