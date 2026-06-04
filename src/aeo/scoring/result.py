"""
Scoring primitives shared by every scorer.

Holds the ``ScoreContext`` (the only input a scorer receives) plus the small
pure helpers for mapping signals onto the 1-5 scale. Kept dependency-light so
scorers and the aggregator can both import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..nlp.llm import LLMClient
from ..storage.models import ExtractionBundle
from .rubric import Rubric


@dataclass(slots=True)
class ScoreContext:
    """Everything a scorer is allowed to see. Scorers read the bundle only —
    they never re-parse HTML or hit the network (except the optional LLM)."""

    bundle: ExtractionBundle
    rubric: Rubric
    llm: LLMClient | None = None


def clamp_tier(n: float, lo: int = 1, hi: int = 5) -> int:
    return max(lo, min(hi, round(n)))


def tier_from_thresholds(value: float, tiers: dict[Any, float]) -> int:
    """Highest tier whose minimum threshold is met. ``tiers`` maps tier→min
    value (ascending), e.g. {1: 0, 2: 1, 3: 3, 4: 6, 5: 10}."""
    best = min(int(k) for k in tiers)
    for tier in sorted(tiers, key=lambda k: int(k)):
        if value >= tiers[tier]:
            best = int(tier)
    return best


def priority_tier(total: int, max_possible: int) -> str:
    """Remediation priority — a *low* score is a *high* priority to fix."""
    pct = (total / max_possible * 100) if max_possible else 0.0
    if pct < 35:
        return "critical"
    if pct < 55:
        return "high"
    if pct < 75:
        return "medium"
    return "low"
