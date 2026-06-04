"""
Criterion 5 — Heading Structure.

Base tier from the share of H2/H3 headings phrased as questions, then dock a
point each for a missing H1 or a template H1 ("Resources", "Welcome", …).
Both penalties are real bugs the audit found: the Zero-Days page has no H1,
several product pages render the literal H1 "Resources".
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext, clamp_tier, tier_from_thresholds


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("heading_structure")
    tiers = crit.cfg.get("tiers", {1: 0.0, 2: 0.10, 3: 0.25, 4: 0.45, 5: 0.65})
    pen_missing = int(crit.cfg.get("penalty_missing_h1", 1))
    pen_template = int(crit.cfg.get("penalty_template_h1", 1))

    h = ctx.bundle.get("headings", {}) or {}
    ratio = float(h.get("h23_question_ratio", 0.0) or 0.0)
    base = tier_from_thresholds(ratio, tiers)

    value = base
    penalties: list[str] = []
    if h.get("missing_h1"):
        value -= pen_missing
        penalties.append("missing_h1")
    if h.get("template_h1"):
        value -= pen_template
        penalties.append(f"template_h1 ({h.get('h1_text')!r})")

    value = clamp_tier(value)

    return CriterionScore(
        name="heading_structure",
        value=value,
        evidence={
            "h1_text": h.get("h1_text"),
            "h23_total": h.get("h23_total", 0),
            "h23_question_ratio": round(ratio, 3),
            "base_tier": base,
            "penalties": penalties,
            "hierarchy_ok": h.get("hierarchy_ok"),
        },
        notes="; ".join(penalties) or f"{ratio:.0%} question headings",
    )
