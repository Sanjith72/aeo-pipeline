"""recommendation_outcomes — the Retention Engine's completion log.

When a recommendation is issued we ``open`` a *pending* outcome that pins the
page's content hash at that moment (the baseline). On every later re-crawl
``mark_from_recrawl`` compares the page's current hash against that baseline; a
change flips the outcome to ``implemented`` — the system's real "did they do it?"
signal, and the join target for the implementation-rate metric (Block F).

Identity is the stable ``url_normalized``, not ``page_id`` (crawled_pages is
upserted per run, so its id changes every run — see migration 0010).

``decide_status`` is a pure function (no DB) so the issue → unchanged → changed
lifecycle is unit-testable offline, mirroring ``feedback._check_status``.
"""

from __future__ import annotations

from typing import Any

from ..db import transaction

# recommendation_outcomes.status vocabulary (must satisfy the DB CHECK).
PENDING = "pending"
IMPLEMENTED = "implemented"
REGRESSED = "regressed"
NOT_DETECTED = "not_detected"

# detection_method values — how an 'implemented' verdict was reached.
METHOD_HASH_CHANGED = "content_hash_changed"


def decide_status(baseline_hash: str | None, current_hash: str | None) -> str | None:
    """Pure completion decision for one pending outcome.

    Returns the NEW status, or ``None`` to mean "no change — leave it pending".

    A differing content hash means the watched page changed since we recommended
    the edit → ``implemented``. We deliberately stop here: a hash change proves the
    page MOVED, not that this specific criterion was satisfied. Confirming the
    latter from the hash alone is the circular self-grading the v4 validator exists
    to avoid, so the criterion-plausibility check is left as a TODO hook in
    ``mark_from_recrawl`` rather than folded in here.
    """
    if not baseline_hash or not current_hash:
        return None  # can't compare → keep watching
    if current_hash == baseline_hash:
        return None  # unchanged → still pending
    return IMPLEMENTED


def open(
    rec_id: int,
    page_id: int | None,
    url_normalized: str,
    baseline_run_id: int | None,
    baseline_hash: str | None,
    criterion: str | None = None,
) -> int:
    """Open a pending outcome for a freshly-issued recommendation. Returns its id."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO recommendation_outcomes (
                rec_id, page_id, url_normalized, criterion,
                baseline_run_id, baseline_hash, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                rec_id, page_id, url_normalized, criterion,
                baseline_run_id, baseline_hash, PENDING,
            ),
        )
        return cur.fetchone()["id"]


def mark_from_recrawl(
    url_normalized: str,
    current_run_id: int,
    current_hash: str | None,
) -> int:
    """Re-crawl bookkeeping: for every PENDING outcome on this URL, compare the
    current content hash against the pinned baseline. A change flips the outcome to
    ``implemented`` with the detecting run + timestamp. Returns how many flipped.

    Keyed on ``url_normalized`` (stable across runs), not the per-run page id.
    """
    flipped = 0
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, baseline_hash FROM recommendation_outcomes "
            "WHERE url_normalized = %s AND status = %s",
            (url_normalized, PENDING),
        )
        rows = cur.fetchall()
        for row in rows:
            new_status = decide_status(row["baseline_hash"], current_hash)
            if new_status is None:
                continue
            # TODO(retention): gate this on a criterion-plausibility verifier before
            # claiming the criterion was actually satisfied (avoid self-grading).
            cur.execute(
                """
                UPDATE recommendation_outcomes
                SET status = %s,
                    detection_method = %s,
                    detected_run_id = %s,
                    detected_at = NOW()
                WHERE id = %s
                """,
                (new_status, METHOD_HASH_CHANGED, current_run_id, row["id"]),
            )
            flipped += 1
    return flipped


def for_page(page_id: int, status: str | None = None) -> list[dict[str, Any]]:
    """Outcomes recorded against a page row (provenance view), newest first."""
    sql = "SELECT * FROM recommendation_outcomes WHERE page_id = %s"
    params: list[Any] = [page_id]
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY id DESC"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [dict(row) for row in cur.fetchall()]


def pending_for_url(url_normalized: str) -> list[dict[str, Any]]:
    """The pending outcomes a re-crawl of this URL would evaluate."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM recommendation_outcomes "
            "WHERE url_normalized = %s AND status = %s ORDER BY id",
            (url_normalized, PENDING),
        )
        return [dict(row) for row in cur.fetchall()]
