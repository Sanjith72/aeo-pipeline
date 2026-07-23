"""skill_scores — the v5 five-skill derived layer (CH-04). One row per (page, run,
skills_version); the six score columns are the queryable summary, ``detail`` holds the
full per-skill payload (suggestions + evidence + the impact-ranked priorities).

Mirrors the rubric_scores_v2 repo's upsert shape but is entirely separate — this layer is
derived and separately versioned, so writing it never touches the frozen rubric contract.
"""

from __future__ import annotations

import json

from ..db import transaction


def put(page_id: int, run_id: int, payload: dict) -> int:
    """Upsert the skill scores for a page/run. ``payload`` is
    :func:`aeo.scoring.skills.build_skill_scores` output."""
    skills = payload.get("skills", {}) or {}

    def _score(name: str) -> int:
        return int((skills.get(name) or {}).get("score", 0))

    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO skill_scores (
                page_id, run_id, skills_version,
                messaging_score, conversion_score, discovery_visibility_score,
                proof_trust_score, structure_ux_score, overall_score,
                detail
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s::jsonb
            )
            ON CONFLICT (page_id, run_id, skills_version) DO UPDATE SET
                messaging_score            = EXCLUDED.messaging_score,
                conversion_score           = EXCLUDED.conversion_score,
                discovery_visibility_score = EXCLUDED.discovery_visibility_score,
                proof_trust_score          = EXCLUDED.proof_trust_score,
                structure_ux_score         = EXCLUDED.structure_ux_score,
                overall_score              = EXCLUDED.overall_score,
                detail                     = EXCLUDED.detail,
                scored_at                  = NOW()
            RETURNING id
            """,
            (
                page_id, run_id, payload.get("skills_version", "1.0"),
                _score("messaging"), _score("conversion"), _score("discovery_visibility"),
                _score("proof_trust"), _score("structure_ux"), int(payload.get("overall", 0)),
                json.dumps({"skills": skills, "priorities": payload.get("priorities", [])}, default=str),
            ),
        )
        return cur.fetchone()["id"]


def latest_for_url(url_normalized: str) -> dict | None:
    """The most recent skill-score row for a URL (across runs) — the before/after
    baseline source for ticket verification (CH-15)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.*
            FROM skill_scores s JOIN crawled_pages p ON p.id = s.page_id
            WHERE p.url_normalized = %s
            ORDER BY s.scored_at DESC
            LIMIT 1
            """,
            (url_normalized,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
