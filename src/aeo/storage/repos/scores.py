"""rubric_scores_v2 — 10-criterion 1-5 scores (v3; was 8)."""

from __future__ import annotations

import json

from ..db import transaction
from ..models import PageScore


def _tier(score: PageScore, name: str) -> int | None:
    """Per-criterion tier, or None when the criterion is absent. The two v3
    columns are nullable, so a pre-v3 score (8 criteria) still inserts cleanly."""
    crit = score.criteria.get(name)
    return crit.value if crit is not None else None


def put(score: PageScore, scored_by: str) -> int:
    crit = score.criteria
    evidence = {name: {"value": c.value, "notes": c.notes, **c.evidence}
                for name, c in crit.items()}

    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rubric_scores_v2 (
                page_id, run_id, rubric_version,
                schema_markup_score, qa_blocks_score, stats_in_html_score,
                entity_consistency_score, heading_structure_score, content_depth_score,
                citation_signals_score, load_speed_score,
                render_accessibility_score, answer_readability_score,
                total_score, max_possible_score, priority_tier,
                evidence, scored_by
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s::jsonb, %s
            )
            ON CONFLICT (page_id, run_id, rubric_version) DO UPDATE SET
                schema_markup_score        = EXCLUDED.schema_markup_score,
                qa_blocks_score            = EXCLUDED.qa_blocks_score,
                stats_in_html_score        = EXCLUDED.stats_in_html_score,
                entity_consistency_score   = EXCLUDED.entity_consistency_score,
                heading_structure_score    = EXCLUDED.heading_structure_score,
                content_depth_score        = EXCLUDED.content_depth_score,
                citation_signals_score     = EXCLUDED.citation_signals_score,
                load_speed_score           = EXCLUDED.load_speed_score,
                render_accessibility_score = EXCLUDED.render_accessibility_score,
                answer_readability_score   = EXCLUDED.answer_readability_score,
                total_score                = EXCLUDED.total_score,
                priority_tier              = EXCLUDED.priority_tier,
                evidence                   = EXCLUDED.evidence,
                scored_by                  = EXCLUDED.scored_by,
                scored_at                  = NOW()
            RETURNING id
            """,
            (
                score.page_id, score.run_id, score.rubric_version,
                _tier(score, "schema_markup"),
                _tier(score, "qa_blocks"),
                _tier(score, "stats_in_html"),
                _tier(score, "entity_consistency"),
                _tier(score, "heading_structure"),
                _tier(score, "content_depth"),
                _tier(score, "citation_signals"),
                _tier(score, "load_speed"),
                _tier(score, "render_accessibility"),
                _tier(score, "answer_readability"),
                score.total, score.max_possible, score.priority_tier,
                json.dumps(evidence, default=str), scored_by,
            ),
        )
        return cur.fetchone()["id"]


def copy_latest_for_url(url_normalized: str, new_page_id: int, new_run_id: int) -> bool:
    """Clone the most recent prior score for this URL onto a new page/run.
    Pairs with extractions.copy_latest_for_url for the unchanged-content
    short-circuit. Returns True if a score was copied."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rubric_scores_v2 (
                page_id, run_id, rubric_version,
                schema_markup_score, qa_blocks_score, stats_in_html_score,
                entity_consistency_score, heading_structure_score, content_depth_score,
                citation_signals_score, load_speed_score,
                render_accessibility_score, answer_readability_score,
                total_score, max_possible_score, priority_tier, evidence, scored_by
            )
            SELECT %(new_page)s, %(new_run)s, s.rubric_version,
                s.schema_markup_score, s.qa_blocks_score, s.stats_in_html_score,
                s.entity_consistency_score, s.heading_structure_score, s.content_depth_score,
                s.citation_signals_score, s.load_speed_score,
                s.render_accessibility_score, s.answer_readability_score,
                s.total_score, s.max_possible_score, s.priority_tier, s.evidence, s.scored_by
            FROM rubric_scores_v2 s
            JOIN crawled_pages p ON p.id = s.page_id
            WHERE p.url_normalized = %(url)s AND s.page_id <> %(new_page)s
            ORDER BY s.scored_at DESC
            LIMIT 1
            ON CONFLICT (page_id, run_id, rubric_version) DO NOTHING
            RETURNING id
            """,
            {"new_page": new_page_id, "new_run": new_run_id, "url": url_normalized},
        )
        return cur.fetchone() is not None


def run_report(run_id: int) -> dict:
    """Aggregate scores for a run: totals, distribution, and worst pages —
    the numbers the `aeo status` command prints."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)                       AS pages,
                   ROUND(AVG(total_score), 1)     AS avg_total,
                   MIN(total_score)               AS min_total,
                   MAX(total_score)               AS max_total,
                   ROUND(AVG(score_percentage),1) AS avg_pct
            FROM rubric_scores_v2 WHERE run_id = %s
            """,
            (run_id,),
        )
        aggregate = cur.fetchone()

        cur.execute(
            "SELECT priority_tier, COUNT(*) AS n FROM rubric_scores_v2 "
            "WHERE run_id = %s GROUP BY priority_tier ORDER BY n DESC",
            (run_id,),
        )
        by_priority = {row["priority_tier"]: row["n"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT p.url, s.total_score, s.priority_tier
            FROM rubric_scores_v2 s JOIN crawled_pages p ON p.id = s.page_id
            WHERE s.run_id = %s
            ORDER BY s.total_score ASC LIMIT 10
            """,
            (run_id,),
        )
        worst = [dict(row) for row in cur.fetchall()]

    return {"aggregate": dict(aggregate) if aggregate else {}, "by_priority": by_priority, "worst_pages": worst}


_TIER_COLS = (
    "schema_markup_score, qa_blocks_score, stats_in_html_score, "
    "entity_consistency_score, heading_structure_score, content_depth_score, "
    "citation_signals_score, load_speed_score, render_accessibility_score, "
    "answer_readability_score, total_score"
)


def scored_pages_for_run(run_id: int, *, owner: str | None = None, limit: int = 100_000) -> list[dict]:
    """(page_id, url) for every scored page in a run, optionally filtered to a
    client- or competitor-owned page. Drives the standalone analysis phase."""
    where = "WHERE s.run_id = %s"
    if owner == "client":
        where += " AND cp.client_id IS NOT NULL"
    elif owner == "competitor":
        where += " AND cp.competitor_id IS NOT NULL"
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT cp.id AS page_id, cp.url
            FROM rubric_scores_v2 s JOIN crawled_pages cp ON cp.id = s.page_id
            {where}
            ORDER BY cp.id
            LIMIT %s
            """,
            (run_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def latest_competitor_scores(limit: int = 500) -> list[dict]:
    """The most recent score per competitor page (across all runs), with its
    per-criterion tiers — the candidate pool for the gap analysis's 40% layer."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON (s.page_id)
                   s.page_id, cp.url, {_TIER_COLS}
            FROM rubric_scores_v2 s JOIN crawled_pages cp ON cp.id = s.page_id
            WHERE cp.competitor_id IS NOT NULL
            ORDER BY s.page_id, s.scored_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def pages_pending_score(run_id: int, limit: int = 50) -> list[int]:
    """Page IDs that have an extraction bundle but no score yet (for this run)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cp.id
            FROM crawled_pages cp
            JOIN page_extractions x ON x.page_id = cp.id
            LEFT JOIN rubric_scores_v2 s
                ON s.page_id = cp.id AND s.run_id = cp.run_id
            WHERE cp.run_id = %s
              AND cp.crawl_status = 'success'
              AND s.id IS NULL
            ORDER BY cp.id
            LIMIT %s
            """,
            (run_id, limit),
        )
        return [row["id"] for row in cur.fetchall()]
