"""Crawl-run lifecycle (start, finish, fail)."""

from __future__ import annotations

import uuid
from datetime import datetime

from ..db import transaction
from ..models import CrawlRun


def new_run_key() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return f"run_{ts}_{uuid.uuid4().hex[:6]}"


def start(label: str | None = None, run_key: str | None = None) -> CrawlRun:
    key = run_key or new_run_key()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crawl_runs (run_key, label) VALUES (%s, %s) "
            "RETURNING id, run_key, label, started_at, status",
            (key, label),
        )
        row = cur.fetchone()
    return CrawlRun(
        id=row["id"], run_key=row["run_key"], label=row["label"],
        started_at=row["started_at"], status=row["status"],
    )


def finish(run_id: int, status: str = "succeeded", notes: str | None = None) -> None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE crawl_runs SET finished_at = NOW(), status = %s, notes = %s WHERE id = %s",
            (status, notes, run_id),
        )


def get(run_id: int) -> CrawlRun | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_key, label, started_at, status FROM crawl_runs WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return CrawlRun(**{k: row[k] for k in ("id", "run_key", "label", "started_at", "status")})


def latest_for_domain(domain: str) -> dict | None:
    """The most recent crawl run that touched this domain's pages, with when it ran —
    drives the 'Last reviewed N days ago' / use-existing affordance (Task 3, Slice 2b).
    Read-only JOIN; crawl_runs has no owner column, so we key off the stable
    ``crawled_pages.url_normalized`` host. Returns ``{run_id, last_crawled_at, status}``
    or ``None`` when the domain has never been crawled."""
    from ...utils.url import host_of, normalize

    host = host_of(normalize(domain if "://" in domain else f"https://{domain}"))
    if not host:
        return None
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cp.run_id AS run_id, MAX(cp.crawled_at) AS last_crawled_at, r.status AS status
            FROM crawled_pages cp
            JOIN crawl_runs r ON r.id = cp.run_id
            WHERE cp.url_normalized LIKE %s OR cp.url_normalized LIKE %s
            GROUP BY cp.run_id, r.status
            ORDER BY last_crawled_at DESC
            LIMIT 1
            """,
            (f"https://{host}%", f"http://{host}%"),
        )
        row = cur.fetchone()
        return dict(row) if row else None
