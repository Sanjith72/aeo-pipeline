"""crawled_pages — upserts + content-hash lookups."""

from __future__ import annotations

from ..db import transaction
from ..models import FetchedPage, StoredPage

_UPSERT = """
INSERT INTO crawled_pages (
    client_id, competitor_id, run_id,
    url, url_normalized, content_hash,
    raw_html, markdown_content, page_title, meta_description,
    http_status, fetch_duration_ms, crawl_status, error_message
) VALUES (
    %(client_id)s, %(competitor_id)s, %(run_id)s,
    %(url)s, %(url_normalized)s, %(content_hash)s,
    %(raw_html)s, %(markdown_content)s, %(page_title)s, %(meta_description)s,
    %(http_status)s, %(fetch_duration_ms)s, %(crawl_status)s, %(error_message)s
)
ON CONFLICT (url_normalized, run_id) DO UPDATE SET
    content_hash       = EXCLUDED.content_hash,
    raw_html           = EXCLUDED.raw_html,
    markdown_content   = EXCLUDED.markdown_content,
    page_title         = EXCLUDED.page_title,
    meta_description   = EXCLUDED.meta_description,
    http_status        = EXCLUDED.http_status,
    fetch_duration_ms  = EXCLUDED.fetch_duration_ms,
    crawl_status       = EXCLUDED.crawl_status,
    error_message      = EXCLUDED.error_message,
    crawled_at         = NOW()
RETURNING id, url, url_normalized, content_hash, crawl_status
"""


def upsert(
    page: FetchedPage,
    run_id: int,
    client_id: int | None,
    competitor_id: int | None,
) -> StoredPage:
    params = {
        "client_id": client_id,
        "competitor_id": competitor_id,
        "run_id": run_id,
        "url": page.url,
        "url_normalized": page.url_normalized,
        "content_hash": page.content_hash,
        "raw_html": page.html,
        "markdown_content": page.markdown,
        "page_title": (page.title or "")[:512],
        "meta_description": page.meta_description,
        "http_status": page.http_status,
        "fetch_duration_ms": page.fetch_duration_ms,
        "crawl_status": "success" if page.success else "failed",
        "error_message": page.error,
    }
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(_UPSERT, params)
        row = cur.fetchone()
    return StoredPage(
        id=row["id"], url=row["url"], url_normalized=row["url_normalized"],
        content_hash=row["content_hash"], crawl_status=row["crawl_status"],
    )


def last_hash(url_normalized: str) -> str | None:
    """Most recent content hash seen for this URL (any run)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM crawled_pages "
            "WHERE url_normalized = %s AND content_hash IS NOT NULL "
            "ORDER BY crawled_at DESC LIMIT 1",
            (url_normalized,),
        )
        row = cur.fetchone()
    return row["content_hash"] if row else None


def last_crawled_at(url_normalized: str):
    """Timestamp this URL was most recently crawled (any run), or None if never.
    Powers the R2-2 cache-age surface ("data from N hours ago") + the re-crawl prompt."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT crawled_at FROM crawled_pages "
            "WHERE url_normalized = %s AND crawled_at IS NOT NULL "
            "ORDER BY crawled_at DESC LIMIT 1",
            (url_normalized,),
        )
        row = cur.fetchone()
    return row["crawled_at"] if row else None


def get(page_id: int) -> dict | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM crawled_pages WHERE id = %s", (page_id,))
        return cur.fetchone()


def by_run(run_id: int) -> list[dict]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM crawled_pages WHERE run_id = %s ORDER BY id", (run_id,),
        )
        return cur.fetchall()
