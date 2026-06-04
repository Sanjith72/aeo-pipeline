"""
Scorer registry.

Maps each rubric criterion name to its scoring function. The keys here are a
hard contract: storage/repos/scores.py indexes the resulting dict by these
exact names to fill the rubric_scores_v2 columns. Adding a criterion = add a
module + register it here + add a column/migration.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ...logging import get_logger
from ...storage.models import CriterionScore
from ..result import ScoreContext
from . import (
    answer_readability,
    citation_signals,
    content_depth,
    entity_consistency,
    heading_structure,
    load_speed,
    qa_blocks,
    render_accessibility,
    schema_markup,
    stats_in_html,
)

log = get_logger(__name__)

# Criteria 1-8 are the shipped hard contract (storage/repos/scores.py and the
# rubric_scores_v2 columns index these exact names). Criteria 9-10 were added
# in v3 (migration 0008 added their columns).
SCORERS: dict[str, Callable[[ScoreContext], CriterionScore]] = {
    "schema_markup": schema_markup.score,
    "qa_blocks": qa_blocks.score,
    "stats_in_html": stats_in_html.score,
    "entity_consistency": entity_consistency.score,
    "heading_structure": heading_structure.score,
    "content_depth": content_depth.score,
    "citation_signals": citation_signals.score,
    "load_speed": load_speed.score,
    "render_accessibility": render_accessibility.score,
    "answer_readability": answer_readability.score,
}


def _run_one(name: str, fn: Callable[[ScoreContext], CriterionScore], ctx: ScoreContext) -> CriterionScore:
    """Run one scorer with the shared failure-isolation contract: a raising scorer
    floors to the minimum tier with the error recorded."""
    try:
        return fn(ctx)
    except Exception as exc:  # isolate per-criterion failures
        log.warning("scorer_failed", criterion=name, page_id=ctx.bundle.page_id, error=str(exc))
        return CriterionScore(
            name=name,
            value=ctx.rubric.scale_min,
            evidence={"error": str(exc)},
            notes="scorer error → floored",
            scored_by="error",
        )


def run_all(ctx: ScoreContext) -> dict[str, CriterionScore]:
    """Run every scorer sequentially. A scorer that raises is isolated (floored),
    so one bad page never aborts a run."""
    return {name: _run_one(name, fn, ctx) for name, fn in SCORERS.items()}


def run_all_parallel(ctx: ScoreContext, *, max_workers: int = 8) -> dict[str, CriterionScore]:
    """v4 Parallel Processor: run the scorers concurrently in a thread pool.

    Output is **identical** to :func:`run_all` — scorers are pure functions over a
    shared, read-only :class:`ScoreContext` (they never mutate the bundle), and the
    result dict is re-assembled in the fixed ``SCORERS`` order, so determinism and
    the hard 10-key contract hold. The win is on the I/O-bound, LLM-refined
    criteria (content_depth, stats), whose model calls release the GIL while in
    flight; CPU-bound scorers see no penalty worth measuring."""
    items = list(SCORERS.items())
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scorer") as pool:
        scored = list(pool.map(lambda it: (it[0], _run_one(it[0], it[1], ctx)), items))
    by_name = dict(scored)
    return {name: by_name[name] for name, _ in items}  # fixed order = determinism
