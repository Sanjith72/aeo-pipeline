"""
Per-recommendation PREDICTED score lift — deterministic, simulate-native.

Feature #2 shows a "+X pts" estimate next to each recommendation *before* the
user acts, so they can pick high-impact fixes rather than just quick ones. The
estimate must stay deterministic (no live LLM call) per the repo's
deterministic-first principle, and it reuses the Validation loop's existing
simulate -> re-score machinery rather than inventing a parallel scorer.

The honest computation, in one pass:

  * **Cumulative-marginal, in gap-priority order.** Each rec is applied to one
    evolving synthetic bundle (highest-priority criterion first); a rec's point
    estimate is the *marginal* rise in the page total it adds on top of the recs
    before it. This resolves the N-recs-per-criterion case (``schema_markup`` can
    emit up to four recs but a criterion has only one tier of headroom): the
    first rec to fill the headroom earns it, later siblings honestly score 0.
    Per-rec points therefore sum to the page's aggregate simulated delta — no
    double-counting.

  * **Tier-short band.** The appliers are bounded to the Reference target (an
    optimistic cap), so the realistic downside is *not fully reaching* it. The
    band is ``[max(0, point - weight), point]`` — one tier short on the low end.

  * **Three honest states.** ``simulated`` (a real positive estimate),
    ``no_deterministic_lift`` (the rec applied a signal but the total did not move
    — already at/above target, or competitor pressure), and ``unknown`` (an
    advisory-only rec with no concrete artifact to apply; render "—", never a
    fake 0).

Unit is **rubric points** (the page total's native 0-50 scale; all criterion
weights are 1.0, so a tier gain a->b is exactly ``b-a`` points). Pure and
deterministic: it only deep-copies the bundle, calls ``apply_recommendation`` and
``score_fn`` with a disabled LLM, and reads rubric weights — no DB, no network,
no randomness or time.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from pydantic import BaseModel, Field

from ..nlp.llm import LLMClient
from ..processor import GapResult
from ..recommender import Recommendation
from ..scoring import score_page
from ..scoring.rubric import Rubric
from ..storage.models import ExtractionBundle, PageScore
from .simulate import apply_recommendation

# predicted_basis vocabulary (mirrors the recommendations.predicted_basis column).
BASIS_SIMULATED = "simulated"
BASIS_NO_LIFT = "no_deterministic_lift"
BASIS_UNKNOWN = "unknown"

# Native unit of the estimate — the page total's weighted tier-points.
UNIT_RUBRIC_POINTS = "rubric_points"

_ROUND = 3

# score_page-compatible callable, so the validator can pass its own (and tests can
# inject a stub) without this module hard-depending on the aggregator at call time.
ScoreFn = Callable[..., PageScore]


class PredictedLift(BaseModel):
    """The deterministic predicted lift for one recommendation, as carried in the
    payload and persisted on the recommendation row.

    ``point``/``low``/``high`` are ``None`` only when ``basis`` is ``unknown`` (the
    simulator could not estimate). A real-but-zero estimate (``no_deterministic_lift``)
    keeps numeric ``0.0`` values so the distinction survives into calibration."""

    point: float | None = Field(default=None, description="point estimate, rubric points")
    low: float | None = Field(default=None, description="tier-short conservative bound")
    high: float | None = Field(default=None, description="optimistic (bounded-to-target) bound")
    unit: str = UNIT_RUBRIC_POINTS
    basis: str = BASIS_UNKNOWN

    @classmethod
    def unknown(cls) -> PredictedLift:
        """Advisory-only rec — nothing concrete to simulate. Renders as '—'."""
        return cls(point=None, low=None, high=None, basis=BASIS_UNKNOWN)


def _weight(rubric: Rubric, criterion: str | None) -> float:
    """Rubric weight for a criterion (1.0 for the default rubric / unknown names)."""
    if criterion and criterion in rubric.criteria:
        return rubric.get(criterion).weight
    return 1.0


def _priority_order(recs: list[Recommendation], gap: GapResult | None) -> list[int]:
    """Indices of ``recs`` ordered by their criterion's rank in the gap's
    priority-sorted deficiency list (most impactful first); recs whose criterion is
    off the list keep their original relative order. Stable, so same-criterion recs
    (e.g. several ``schema_markup`` blocks) keep their emission order."""
    if gap is None:
        return list(range(len(recs)))
    rank = {g.criterion: i for i, g in enumerate(gap.criterion_gaps)}
    fallback = len(rank)
    return sorted(range(len(recs)), key=lambda i: rank.get(recs[i].criterion or "", fallback))


def predict_lifts(
    bundle: ExtractionBundle,
    recs: list[Recommendation],
    *,
    rubric: Rubric,
    reference: object,
    run_id: int,
    baseline_total: int,
    gap: GapResult | None = None,
    llm: LLMClient | None = None,
    score_fn: ScoreFn = score_page,
) -> list[PredictedLift]:
    """Predicted rubric-point lift for each rec in ``recs``, aligned to its order.

    Applies the recs cumulatively (gap-priority order) to one synthetic copy of
    ``bundle`` and reads each rec's marginal contribution to the page total. The
    result list is in the SAME order as ``recs`` (so it lines up with persistence
    and ``ValidationOutcome.recommendations``).

    ``llm`` MUST be a disabled client (the validator passes its ``_DETERMINISTIC_LLM``)
    so the re-score measures the edit's effect, never LLM scoring noise. ``score_fn``
    defaults to the real scorer; tests may inject a stub.
    """
    if not recs:
        return []

    results: list[PredictedLift | None] = [None] * len(recs)
    synthetic = copy.deepcopy(bundle)
    running = float(baseline_total)

    for i in _priority_order(recs, gap):
        rec = recs[i]
        changed = apply_recommendation(synthetic, rec, rubric=rubric, reference=reference)
        if not changed:
            # Advisory / no applier / already-applied signal — nothing to measure.
            results[i] = PredictedLift.unknown()
            continue
        new_total = float(score_fn(synthetic, run_id, llm=llm, rubric=rubric).total)
        marginal = round(new_total - running, _ROUND)
        running = new_total
        if marginal > 0:
            weight = _weight(rubric, rec.criterion)
            low = round(max(0.0, marginal - weight), _ROUND)
            results[i] = PredictedLift(
                point=marginal, low=low, high=marginal, basis=BASIS_SIMULATED
            )
        else:
            # The signal moved but the deterministic total did not (a sibling rec
            # already filled this criterion's headroom, or the page is at/above
            # target under competitor pressure). An honest, calibratable zero.
            results[i] = PredictedLift(point=0.0, low=0.0, high=0.0, basis=BASIS_NO_LIFT)

    # Every index is visited exactly once (the order is a permutation), so no slot
    # is left unset; fall back defensively to unknown to satisfy the type checker.
    return [r if r is not None else PredictedLift.unknown() for r in results]
