"""gamification — derived companion state + an idempotent award ledger.

State is keyed on session_id (the auth-free DAU identity). Awards are append-only and unique
on (award_type, source_table, source_id), so reconcile re-runs never double-grant. The
reconciler (companion/rewards.py) is the brain; this module is dumb storage.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def get_state(session_id: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM gamification_state WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_state(
    session_id: str,
    *,
    domain: str | None = None,
    client_id: int | None = None,
    aeo_score: int | None = None,
    aeo_band: str | None = None,
    maturity_stage: str = "foundations",
    momentum: int = 0,
    verified_wins: int = 0,
    citations_earned: int = 0,
    track_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or fully replace a session's derived state. The reconciler computes every field,
    so this is a straight upsert (no COALESCE games)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gamification_state
                (session_id, domain, client_id, aeo_score, aeo_band, maturity_stage,
                 momentum, verified_wins, citations_earned, track_progress)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (session_id) DO UPDATE SET
                domain           = EXCLUDED.domain,
                client_id        = EXCLUDED.client_id,
                aeo_score        = EXCLUDED.aeo_score,
                aeo_band         = EXCLUDED.aeo_band,
                maturity_stage   = EXCLUDED.maturity_stage,
                momentum         = EXCLUDED.momentum,
                verified_wins    = EXCLUDED.verified_wins,
                citations_earned = EXCLUDED.citations_earned,
                track_progress   = EXCLUDED.track_progress
            RETURNING *
            """,
            (session_id, domain, client_id, aeo_score, aeo_band, maturity_stage,
             momentum, verified_wins, citations_earned, json.dumps(track_progress or {}, default=str)),
        )
        return dict(cur.fetchone())


def grant_award(
    session_id: str,
    *,
    award_type: str,
    source_table: str,
    source_id: int,
    client_id: int | None = None,
    criterion: str | None = None,
    tier_before: int | None = None,
    tier_after: int | None = None,
    score_delta: int | None = None,
    detail: dict[str, Any] | None = None,
) -> int | None:
    """Grant an award idempotently. Returns the new award id, or None if it was already granted
    (the UNIQUE(award_type, source_table, source_id) guard fired)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gamification_awards
                (session_id, client_id, award_type, source_table, source_id,
                 criterion, tier_before, tier_after, score_delta, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (award_type, source_table, source_id) DO NOTHING
            RETURNING id
            """,
            (session_id, client_id, award_type, source_table, source_id,
             criterion, tier_before, tier_after, score_delta, json.dumps(detail or {}, default=str)),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def awards_for(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM gamification_awards WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
            (session_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def unlock_achievement(session_id: str, code: str, *, source_run_id: int | None = None) -> bool:
    """Earn a status tier once. Returns True only on the first unlock for this session+code."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO achievement_unlocks (session_id, code, source_run_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id, code) DO NOTHING RETURNING id",
            (session_id, code, source_run_id),
        )
        return cur.fetchone() is not None
