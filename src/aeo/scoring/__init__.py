"""Rubric scoring engine — 8 criteria, deterministic-first, 1-5 scale."""

from .aggregator import RUBRIC_VERSION, score_page
from .result import ScoreContext, priority_tier
from .rubric import Criterion, Rubric, load_rubric
from .scorers import SCORERS, run_all

__all__ = [
    "RUBRIC_VERSION",
    "SCORERS",
    "Criterion",
    "Rubric",
    "ScoreContext",
    "load_rubric",
    "priority_tier",
    "run_all",
    "score_page",
]
