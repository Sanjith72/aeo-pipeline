"""
Criterion 9 (v3) — Render Accessibility.

Answer engines often read the *raw* HTML, not a JS-executed DOM. Content that
only appears after client-side rendering is invisible to them — an AEO problem
independent of page speed. Wraps the ``render_mode`` extractor:

  - ``js_only_content`` (almost nothing in raw HTML, lots after render) → tier 1.
  - otherwise map the ``inflation_ratio`` (rendered/initial text) onto tiers —
    LOWER inflation is better (the content is already in the HTML).

When render data is missing the score is neutral (3), never punitive — mirrors
the load_speed convention.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext

_NEUTRAL = 3
_DEFAULT_INFLATION_MAX = {5: 1.5, 4: 2.5, 3: 4.0, 2: 8.0}


def _tier_from_inflation(inflation: float, maxes: dict) -> int:
    """Highest tier whose tolerated max inflation is not exceeded; else tier 1."""
    for tier in sorted((int(k) for k in maxes), reverse=True):
        if inflation <= float(maxes[tier]):
            return tier
    return 1


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("render_accessibility")
    maxes = crit.cfg.get("inflation_max", _DEFAULT_INFLATION_MAX)

    render = ctx.bundle.get("render_mode", {}) or {}

    if not render:
        return CriterionScore(
            name="render_accessibility",
            value=_NEUTRAL,
            evidence={"render_data": False},
            notes="render mode unavailable — neutral score",
            scored_by="deterministic",
        )

    js_only = bool(render.get("js_only_content"))
    inflation = float(render.get("inflation_ratio", 1.0) or 1.0)

    if js_only:
        value = 1
        notes = "content invisible without JS (js_only_content)"
    else:
        value = _tier_from_inflation(inflation, maxes)
        notes = f"inflation ratio {inflation:.2f}"

    return CriterionScore(
        name="render_accessibility",
        value=value,
        evidence={
            "js_only_content": js_only,
            "js_dependent": bool(render.get("js_dependent")),
            "inflation_ratio": inflation,
            "initial_text_chars": render.get("initial_text_chars"),
            "rendered_text_chars": render.get("rendered_text_chars"),
        },
        notes=notes,
        scored_by="deterministic",
    )
