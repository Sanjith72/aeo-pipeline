"""
Aggregation — turn the criterion scores into one PageScore.

Total is the weighted sum of the 1-5 tiers (weights default to 1.0; the v3
10-criterion rubric → max 50). Per-criterion tiers are stored raw (1-5); only
the total reflects weighting. ``max_possible`` is derived from the rubric, so it
tracks the criterion count automatically.
"""

from __future__ import annotations

from ..nlp.llm import LLMClient, get_client
from ..storage.models import ExtractionBundle, PageScore
from .result import ScoreContext, priority_tier
from .rubric import Rubric, load_rubric
from .scorers import run_all, run_all_parallel

RUBRIC_VERSION = "2.0"  # v3 build: 10 criteria (was "1.0" = 8 criteria)


def score_page(
    bundle: ExtractionBundle,
    run_id: int,
    *,
    llm: LLMClient | None = None,
    rubric: Rubric | None = None,
    parallel: bool = False,
    max_workers: int = 8,
) -> PageScore:
    rubric = rubric or load_rubric()
    if llm is None:
        llm = get_client()  # disabled clients no-op; scorers handle that

    ctx = ScoreContext(bundle=bundle, rubric=rubric, llm=llm)
    # v4 Parallel Processor (opt-in): identical output, faster on LLM criteria.
    criteria = run_all_parallel(ctx, max_workers=max_workers) if parallel else run_all(ctx)

    total = 0.0
    max_possible = 0.0
    for name, crit in criteria.items():
        weight = rubric.get(name).weight if name in rubric.criteria else 1.0
        total += crit.value * weight
        max_possible += rubric.scale_max * weight

    total_i = round(total)
    max_i = round(max_possible)

    return PageScore(
        page_id=bundle.page_id,
        run_id=run_id,
        criteria=criteria,
        total=total_i,
        max_possible=max_i,
        priority_tier=priority_tier(total_i, max_i),
        rubric_version=RUBRIC_VERSION,
    )
