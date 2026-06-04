"""
feedback — the validated-wins loop's persistence.

Two tables: ``citation_results`` (did a page get cited for its question?) and
``criteria_refinements`` (human-gated proposals to nudge a criterion's target).
SQL only; the proposal *logic* lives in ``reference.feedback``.
"""

from __future__ import annotations

import json

from ...reference.feedback import CitationObservation, CriteriaRefinement
from ..db import transaction

# criteria_refinements.status mirrors the DB CHECK constraint (migration 0009).
# Validate in Python so a bad value fails with a clear error instead of a raw
# Postgres CHECK violation at write time.
_VALID_REFINEMENT_STATUSES = frozenset({"proposed", "accepted", "rejected"})


def _check_status(status: str) -> str:
    if status not in _VALID_REFINEMENT_STATUSES:
        raise ValueError(
            f"invalid refinement status {status!r}; "
            f"must be one of {sorted(_VALID_REFINEMENT_STATUSES)}"
        )
    return status


# ── citation_results ──────────────────────────────────────────────────────────


def record_citation(
    *,
    page_id: int | None,
    run_id: int | None,
    url: str,
    question: str,
    cited: bool,
    engine: str = "perplexity",
    evidence: dict | None = None,
) -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO citation_results (page_id, run_id, url, question, cited, engine, evidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (page_id, run_id, url, question, cited, engine, json.dumps(evidence or {}, default=str)),
        )
        return cur.fetchone()["id"]


def recent_observations(limit: int = 500) -> list[CitationObservation]:
    """Most-recent citation outcome per page, paired with the rubric tiers from the
    **same run** — the input to :func:`reference.feedback.propose_criteria_refinements`.

    The score is correlated to the citation's own ``run_id`` (a page re-scored in a
    later run, or under a different rubric_version, must not have its outcome judged
    against an unrelated run's tiers). A ``LEFT JOIN`` keeps citations whose page has
    no matching-run score: those surface with empty ``tiers`` and the downstream
    proposer skips them per-criterion (``if criterion in o.tiers``), rather than the
    page vanishing entirely as an INNER JOIN would drop it."""
    from .scores import _TIER_COLS  # reuse the column list (single source of truth)

    tier_names = [c.strip().removesuffix("_score") for c in _TIER_COLS.split(",") if c.strip() != "total_score"]
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON (cr.page_id)
                   cr.page_id, cr.url, cr.cited, {_TIER_COLS}
            FROM citation_results cr
            LEFT JOIN rubric_scores_v2 s
                   ON s.page_id = cr.page_id AND s.run_id = cr.run_id
            WHERE cr.page_id IS NOT NULL
            ORDER BY cr.page_id, cr.created_at DESC, s.scored_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    out: list[CitationObservation] = []
    for row in rows:
        tiers = {name: int(row[f"{name}_score"]) for name in tier_names if row.get(f"{name}_score") is not None}
        out.append(CitationObservation(page_id=row["page_id"], url=row["url"], cited=row["cited"], tiers=tiers))
    return out


# ── criteria_refinements ────────────────────────────────────────────────────────


def save_refinement(ref: CriteriaRefinement) -> int:
    _check_status(ref.status)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO criteria_refinements (
                criterion, current_target, proposed_target, rationale, evidence, status
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                ref.criterion, ref.current_target, ref.proposed_target,
                ref.rationale, json.dumps(ref.evidence, default=str), ref.status,
            ),
        )
        return cur.fetchone()["id"]


def list_refinements(status: str | None = None) -> list[dict]:
    where = "WHERE status = %s" if status else ""
    params: tuple = (status,) if status else ()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM criteria_refinements {where} ORDER BY created_at DESC",
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def set_refinement_status(refinement_id: int, status: str) -> None:
    _check_status(status)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE criteria_refinements SET status = %s, updated_at = NOW() WHERE id = %s",
            (status, refinement_id),
        )
