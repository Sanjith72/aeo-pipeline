"""
Criterion 8 — Load Speed.

PageSpeed Insights mobile performance score mapped to tiers
{1:0, 2:30, 3:50, 4:75, 5:90}. When PSI is disabled (no API key) the score is
neutral (3) rather than punitive. A JS-only-content page is docked a point
either way — content an AI crawler can't see without executing JS is an AEO
problem independent of raw speed.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext, clamp_tier, tier_from_thresholds

_NEUTRAL = 3


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("load_speed")
    tiers = crit.cfg.get("tiers", {1: 0, 2: 30, 3: 50, 4: 75, 5: 90})
    pen_js = int(crit.cfg.get("penalty_js_only_content", 1))

    psi = ctx.bundle.get("pagespeed")
    render = ctx.bundle.get("render_mode", {}) or {}
    js_only = bool(render.get("js_only_content"))

    perf = psi.get("performance_score") if psi else None

    if perf is None:
        value = _NEUTRAL
        note_bits = ["PageSpeed unavailable — neutral score"]
        scored_by = "deterministic"
    else:
        value = tier_from_thresholds(perf, tiers)
        note_bits = [f"PSI mobile performance {perf}"]
        scored_by = "pagespeed"

    if js_only:
        value = clamp_tier(value - pen_js)
        note_bits.append("JS-only content penalty")

    return CriterionScore(
        name="load_speed",
        value=value,
        evidence={
            "psi_available": perf is not None,
            "performance_score": perf,
            "lcp_ms": psi.get("lcp_ms") if psi else None,
            "tbt_ms": psi.get("tbt_ms") if psi else None,
            "cls": psi.get("cls") if psi else None,
            "js_only_content": js_only,
            "inflation_ratio": render.get("inflation_ratio"),
        },
        notes="; ".join(note_bits),
        scored_by=scored_by,
    )
