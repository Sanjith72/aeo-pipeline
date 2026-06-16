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

# detection_method values — how a verdict was reached.
METHOD_HASH_CHANGED = "content_hash_changed"        # legacy; no longer yields a verdict
METHOD_CRITERION_IMPROVED = "criterion_improved"    # the targeted criterion's tier rose
METHOD_CRITERION_REGRESSED = "criterion_regressed"  # the targeted criterion's tier fell


def decide_status(
    baseline_hash: str | None,
    current_hash: str | None,
    *,
    criterion: str | None = None,
    baseline_tier: int | None = None,
    new_tier: int | None = None,
) -> tuple[str | None, str | None]:
    """Pure, honest completion decision for one pending outcome.

    Returns ``(new_status, detection_method)``, or ``(None, None)`` to mean "no verdict
    yet — leave it pending".

    A differing content hash proves the page MOVED, not that the recommended fix landed.
    So ``implemented`` is returned ONLY when the targeted criterion's freshly re-scored
    tier is strictly higher than the tier pinned at issue time (``regressed`` when it
    fell). A hash change we can't tie to a criterion improvement — no criterion, no pinned
    tier, the criterion couldn't be re-scored, or the tier didn't move — keeps the outcome
    pending rather than claiming a verification we can't stand behind. This is the
    criterion-plausibility check that replaces the old self-grading hash-only signal.
    """
    if not baseline_hash or not current_hash:
        return (None, None)  # can't compare → keep watching
    if current_hash == baseline_hash:
        return (None, None)  # unchanged since issue → still pending
    if criterion and baseline_tier is not None and new_tier is not None:
        if new_tier > baseline_tier:
            return (IMPLEMENTED, METHOD_CRITERION_IMPROVED)
        if new_tier < baseline_tier:
            return (REGRESSED, METHOD_CRITERION_REGRESSED)
        return (None, None)  # page changed, but this criterion didn't move → keep watching
    return (None, None)  # a hash-only change is not honest proof the fix landed


def open(
    rec_id: int,
    page_id: int | None,
    url_normalized: str,
    baseline_run_id: int | None,
    baseline_hash: str | None,
    criterion: str | None = None,
    baseline_tier: int | None = None,
) -> int:
    """Open a pending outcome for a freshly-issued recommendation. Returns its id.

    ``baseline_tier`` pins the targeted criterion's score tier at issue time, so a later
    re-crawl can confirm the criterion actually improved (not just that the page changed).
    """
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO recommendation_outcomes (
                rec_id, page_id, url_normalized, criterion,
                baseline_run_id, baseline_hash, baseline_tier, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                rec_id, page_id, url_normalized, criterion,
                baseline_run_id, baseline_hash, baseline_tier, PENDING,
            ),
        )
        return cur.fetchone()["id"]


def mark_from_recrawl(
    url_normalized: str,
    current_run_id: int,
    current_hash: str | None,
    new_tiers: dict[str, int | None] | None = None,
) -> int:
    """Re-crawl bookkeeping: for every PENDING outcome on this URL, decide its verdict
    from the freshly re-scored criterion tiers. ``new_tiers`` maps rubric criterion →
    current tier (from the re-scored page); an outcome flips to ``implemented`` only when
    its targeted criterion's tier rose above the pinned baseline (``regressed`` if it
    fell). Returns how many flipped to a terminal verdict.

    Keyed on ``url_normalized`` (stable across runs), not the per-run page id.
    """
    tiers = new_tiers or {}
    flipped = 0
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, baseline_hash, criterion, baseline_tier "
            "FROM recommendation_outcomes WHERE url_normalized = %s AND status = %s",
            (url_normalized, PENDING),
        )
        rows = cur.fetchall()
        for row in rows:
            criterion = row.get("criterion")
            new_status, method = decide_status(
                row["baseline_hash"], current_hash,
                criterion=criterion,
                baseline_tier=row.get("baseline_tier"),
                new_tier=tiers.get(criterion) if criterion else None,
            )
            if new_status is None:
                continue
            cur.execute(
                """
                UPDATE recommendation_outcomes
                SET status = %s,
                    detection_method = %s,
                    detected_run_id = %s,
                    detected_at = NOW()
                WHERE id = %s
                """,
                (new_status, method, current_run_id, row["id"]),
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
