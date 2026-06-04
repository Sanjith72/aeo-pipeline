"""page_extractions — store the per-page extractor bundle as JSONB."""

from __future__ import annotations

import json

from ..db import transaction
from ..models import ExtractionBundle


def put(bundle: ExtractionBundle, extractor_version: str = "1") -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_extractions (page_id, extracted, extractor_version)
            VALUES (%s, %s::jsonb, %s)
            ON CONFLICT (page_id) DO UPDATE SET
                extracted = EXCLUDED.extracted,
                extractor_version = EXCLUDED.extractor_version,
                extracted_at = NOW()
            RETURNING id
            """,
            (bundle.page_id, json.dumps(bundle.data, default=str), extractor_version),
        )
        return cur.fetchone()["id"]


def copy_latest_for_url(url_normalized: str, new_page_id: int) -> bool:
    """Clone the most recent prior extraction for this URL onto a new page row.
    Used by the fingerprint short-circuit when content is unchanged. Returns
    True if something was copied."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO page_extractions (page_id, extracted, extractor_version)
            SELECT %(new_id)s, x.extracted, x.extractor_version
            FROM page_extractions x
            JOIN crawled_pages p ON p.id = x.page_id
            WHERE p.url_normalized = %(url)s AND x.page_id <> %(new_id)s
            ORDER BY x.extracted_at DESC
            LIMIT 1
            ON CONFLICT (page_id) DO NOTHING
            RETURNING id
            """,
            {"new_id": new_page_id, "url": url_normalized},
        )
        return cur.fetchone() is not None


def get(page_id: int) -> ExtractionBundle | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT page_id, extracted FROM page_extractions WHERE page_id = %s",
            (page_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return ExtractionBundle(page_id=row["page_id"], data=row["extracted"] or {})
