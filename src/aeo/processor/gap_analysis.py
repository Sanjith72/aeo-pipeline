"""
Dual-Layer Gap Analysis — the Processor's deterministic core.

Turns a page's 10-criterion :class:`PageScore` into a prioritized deficiency
list by measuring two normalized gaps:

  * **best-practice gap (60%)** — per-criterion ``max(0, target - actual)``
    against the Reference Layer targets, weighted by rubric weights and
    normalized to ``[0, 1]`` (1 = every criterion at floor relative to target);
  * **competitor gap (40%)** — the same shortfall measured against the *best
    competitor page* for this page's query intent.

``overall_gap`` blends them 0.6 / 0.4. When no competitor exists for the intent
the competitor layer is *absent* (``competitor_gap is None``) and ``overall_gap``
falls back to the best-practice gap alone — a page is never flattered by missing
competitor data. The ordered ``criterion_gaps`` list (largest weighted deficiency
first) is what the Recommender consumes. Pure and deterministic; persistence is a
separate thin helper.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass

from ..reference import Reference, load_reference
from ..scoring.rubric import Rubric, load_rubric
from ..storage.models import PageScore

_BESTPRACTICE_WEIGHT = 0.6
_COMPETITOR_WEIGHT = 0.4
_ROUND = 3


@dataclass(slots=True)
class CompetitorPage:
    """A scored competitor page as the gap analysis consumes it. ``tiers`` maps
    each rubric criterion name to that competitor's 1-5 tier."""

    page_id: int
    intent: str
    total: int
    tiers: Mapping[str, int]


@dataclass(slots=True)
class CriterionGap:
    """One row of the prioritized deficiency list."""

    criterion: str
    actual: int
    target: int
    bestpractice_gap: int       # max(0, target - actual), in tier points
    competitor: int | None      # the best competitor's tier, or None
    competitor_gap: int         # max(0, competitor - actual), in tier points
    weight: float
    priority: float             # weight times blended gap — the ordering key


@dataclass(slots=True)
class GapResult:
    page_id: int
    run_id: int
    bestpractice_gap: float       # normalized [0, 1]
    competitor_gap: float | None  # normalized [0, 1]; None when no competitor
    overall_gap: float            # normalized [0, 1]
    criterion_gaps: list[CriterionGap]
    competitor_page_id: int | None = None
    intent: str | None = None

    def to_detail(self) -> dict:
        """JSONB payload for the gap_analyses.detail column."""
        return {
            "intent": self.intent,
            "competitor_page_id": self.competitor_page_id,
            "competitor_available": self.competitor_gap is not None,
            "weights": {
                "bestpractice": _BESTPRACTICE_WEIGHT,
                "competitor": _COMPETITOR_WEIGHT,
            },
            "criterion_gaps": [asdict(g) for g in self.criterion_gaps],
        }


def select_competitor(
    candidates: Sequence[CompetitorPage], intent: str | None
) -> CompetitorPage | None:
    """The best competitor page for a query intent = the highest-total candidate
    whose intent matches. Returns None when no candidate shares the intent, which
    drops the competitor layer rather than comparing across mismatched intents."""
    matching = [c for c in candidates if intent is not None and c.intent == intent]
    if not matching:
        return None
    return max(matching, key=lambda c: c.total)


def _weight_for(rubric: Rubric, name: str) -> float:
    return rubric.get(name).weight if name in rubric.criteria else 1.0


def analyze_gap(
    score: PageScore,
    *,
    reference: Reference | None = None,
    rubric: Rubric | None = None,
    competitor: CompetitorPage | None = None,
    intent: str | None = None,
) -> GapResult:
    reference = reference or load_reference()
    rubric = rubric or load_rubric()
    scale_min = rubric.scale_min

    have_competitor = competitor is not None

    bp_weighted = bp_max = 0.0
    comp_weighted = comp_max = 0.0
    rows: list[CriterionGap] = []

    for name, crit in score.criteria.items():
        actual = crit.value
        target = reference.target_for(name)
        weight = _weight_for(rubric, name)

        bp_pts = max(0, target - actual)
        bp_weighted += weight * bp_pts
        bp_max += weight * max(0, target - scale_min)

        comp_tier = competitor.tiers.get(name) if have_competitor else None
        comp_pts = max(0, comp_tier - actual) if comp_tier is not None else 0
        if comp_tier is not None:
            comp_weighted += weight * comp_pts
            comp_max += weight * max(0, comp_tier - scale_min)

        # Blended per-criterion priority drives the ordering of the fix list.
        if have_competitor:
            blended = _BESTPRACTICE_WEIGHT * bp_pts + _COMPETITOR_WEIGHT * comp_pts
        else:
            blended = float(bp_pts)
        priority = weight * blended

        if bp_pts > 0 or comp_pts > 0:
            rows.append(
                CriterionGap(
                    criterion=name,
                    actual=actual,
                    target=target,
                    bestpractice_gap=bp_pts,
                    competitor=comp_tier,
                    competitor_gap=comp_pts,
                    weight=weight,
                    priority=round(priority, _ROUND),
                )
            )

    rows.sort(key=lambda g: (-g.priority, g.criterion))

    bestpractice_gap = round(bp_weighted / bp_max, _ROUND) if bp_max > 0 else 0.0
    if not have_competitor:
        competitor_gap: float | None = None
        overall = bestpractice_gap
    else:
        competitor_gap = round(comp_weighted / comp_max, _ROUND) if comp_max > 0 else 0.0
        overall = round(
            _BESTPRACTICE_WEIGHT * bestpractice_gap + _COMPETITOR_WEIGHT * competitor_gap,
            _ROUND,
        )

    return GapResult(
        page_id=score.page_id,
        run_id=score.run_id,
        bestpractice_gap=bestpractice_gap,
        competitor_gap=competitor_gap,
        overall_gap=overall,
        criterion_gaps=rows,
        competitor_page_id=competitor.page_id if have_competitor else None,
        intent=intent,
    )


def persist_gap(result: GapResult) -> int:
    """Write a GapResult to the gap_analyses table. competitor_gap is stored as 0
    when absent (the column is NOT NULL); detail.competitor_available records the
    distinction so consumers can tell 'matched the competitor' from 'no competitor'."""
    from ..storage.repos import gaps as gaps_repo

    return gaps_repo.put(
        page_id=result.page_id,
        run_id=result.run_id,
        bestpractice_gap=result.bestpractice_gap,
        competitor_gap=result.competitor_gap if result.competitor_gap is not None else 0.0,
        overall_gap=result.overall_gap,
        detail=result.to_detail(),
    )
