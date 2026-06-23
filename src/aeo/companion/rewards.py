"""Reward reconciler — grant gamification awards ONLY from real, re-crawl-verified outcomes.

Sources verified wins from outcomes.implemented_for_domain (an outcome flips to 'implemented'
only when a re-crawled criterion's tier rises — never on a manual toggle or a bare content
change), grants them idempotently, and derives per-session state. The AEO Score is passed in
(computed by the frontend's canonical formula), so the backend never invents a number.

Pure-ish + injectable (_gam/_wins) so the logic is unit-testable with no DB. Best-effort: a DB
hiccup must never break the app — callers wrap accordingly.
"""

from __future__ import annotations

from typing import Any


def band(score: int) -> str:
    """The AEO Score band label — mirrors web/lib/score.ts scoreBand()."""
    if score < 40:
        return "Barely visible"
    if score < 60:
        return "On the radar"
    if score < 80:
        return "Recommended"
    return "Top answer"


def maturity(score: int) -> str:
    """Maturity stage from the score. (cited_leader requires a citation — Plan 3B.)"""
    if score < 40:
        return "foundations"
    if score < 60:
        return "on_radar"
    if score < 80:
        return "recommended"
    return "authority"


# AEO-score achievement thresholds (code, min score). Mirrors the 0022 seed.
_SCORE_ACHIEVEMENTS = (("recommended", 60), ("top_answer", 80))


def reconcile(
    session_id: str,
    domain: str | None,
    *,
    aeo_score: int | None = None,
    _gam: Any = None,
    _wins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Grant verified-win awards + score achievements, refresh state. Idempotent. ``_gam`` and
    ``_wins`` are injection seams for tests; in production they default to the real repos."""
    if _gam is None:
        from ..storage.repos import gamification as _gam
    wins = _wins
    if wins is None:
        from ..storage.repos import outcomes as outcomes_repo
        wins = outcomes_repo.implemented_for_domain(domain) if domain else []

    new_awards: list[dict[str, Any]] = []
    for w in wins:
        award_id = _gam.grant_award(
            session_id,
            award_type="verified_win",
            source_table="recommendation_outcomes",
            source_id=w["id"],
            criterion=w.get("criterion"),
            detail={"url": w.get("url_normalized"), "detected_at": str(w.get("detected_at"))},
        )
        if award_id is not None:
            new_awards.append({"award_id": award_id, "criterion": w.get("criterion"),
                               "url": w.get("url_normalized")})

    unlocked: list[str] = []
    if aeo_score is not None:
        for code, threshold in _SCORE_ACHIEVEMENTS:
            if aeo_score >= threshold and _gam.unlock_achievement(session_id, code):
                unlocked.append(code)

    prev = _gam.get_state(session_id) or {}
    momentum = int(prev.get("momentum", 0)) + len(new_awards)
    state = _gam.upsert_state(
        session_id,
        domain=domain,
        aeo_score=aeo_score,
        aeo_band=band(aeo_score) if aeo_score is not None else None,
        maturity_stage=maturity(aeo_score) if aeo_score is not None else "foundations",
        momentum=momentum,
        verified_wins=len(wins),
    )
    return {"new_awards": new_awards, "unlocked": unlocked, "state": state}
