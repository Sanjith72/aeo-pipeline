"""
Unit tests for the per-recommendation PREDICTED lift (Feature #2), DB-free.

Two layers:
  * ``predict_lifts`` — the deterministic cumulative-marginal estimator: a real
    simulated lift, the explicit unknown (advisory) and no-deterministic-lift
    states, the tier-short band, schema N-recs-per-criterion attribution (marginals
    sum to the aggregate, no double-count), input-order alignment, and reproducibility;
  * migration 0019 — asserted by reading its SQL (additive + the right columns).

The live-DB round-trip for the persisted columns lives in the integration smoke.
"""

from __future__ import annotations

import copy
from typing import Any

from aeo.nlp.llm import LLMClient
from aeo.processor import CriterionGap, GapResult
from aeo.recommender.models import CONTENT, SCHEMA, Recommendation
from aeo.reference import load_reference
from aeo.scoring import score_page
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg
from aeo.storage import migrate
from aeo.storage.models import ExtractionBundle
from aeo.validation.predict import (
    BASIS_NO_LIFT,
    BASIS_SIMULATED,
    BASIS_UNKNOWN,
    PredictedLift,
    predict_lifts,
)

RUBRIC = load_rubric()
REFERENCE = load_reference()
DISABLED_LLM = LLMClient(LLMCfg(enabled=False))
RUN_ID = 7


# ---------------------------------------------------------------------------
# fixtures / helpers (mirrors test_validation's style)
# ---------------------------------------------------------------------------


def make_bundle(page_id: int = 1, **parts: Any) -> ExtractionBundle:
    return ExtractionBundle(page_id=page_id, data=dict(parts))


class FakeRef:
    def __init__(self, target: int) -> None:
        self._t = target

    def target_for(self, _criterion: str) -> int:
        return self._t


def content_edit(criterion: str) -> Recommendation:
    return Recommendation(
        rec_type=CONTENT, criterion=criterion,
        title=f"Improve {criterion}", rationale="r", payload={"edits": ["a", "b"]},
    )


def advisory(criterion: str) -> Recommendation:
    return Recommendation(
        rec_type=CONTENT, criterion=criterion,
        title=f"Improve {criterion}", rationale="r", payload={"guidance": "do the thing"},
    )


def schema_rec(stype: str) -> Recommendation:
    return Recommendation(
        rec_type=SCHEMA, criterion="schema_markup",
        title=f"Add {stype}", rationale="r",
        payload={"schema_type": stype, "jsonld": {"@type": stype}},
    )


def gap_with(specs: list[tuple[str, int, int]]) -> GapResult:
    rows = [
        CriterionGap(
            criterion=c, actual=a, target=t, bestpractice_gap=max(0, t - a),
            competitor=None, competitor_gap=0, weight=1.0, priority=float(max(0, t - a)),
        )
        for c, a, t in specs
    ]
    return GapResult(
        page_id=1, run_id=RUN_ID, bestpractice_gap=0.5,
        competitor_gap=None, overall_gap=0.5, criterion_gaps=rows,
    )


def baseline_total(bundle: ExtractionBundle) -> int:
    return score_page(bundle, RUN_ID, llm=DISABLED_LLM, rubric=RUBRIC).total


def single_rec_delta(bundle: ExtractionBundle, rec: Recommendation) -> int:
    """Independent isolated single-rec re-score delta, for cross-checking."""
    from aeo.validation.simulate import apply_recommendation

    before = baseline_total(bundle)
    syn = copy.deepcopy(bundle)
    apply_recommendation(syn, rec, rubric=RUBRIC, reference=REFERENCE)
    return score_page(syn, RUN_ID, llm=DISABLED_LLM, rubric=RUBRIC).total - before


class StubScore:
    def __init__(self, total: float) -> None:
        self.total = total


def stub_score_fn(totals: list[float]):
    """A score_fn returning the given totals in call order (for controlled marginals)."""
    it = iter(totals)

    def fn(_bundle: Any, _run_id: int, *, llm: Any = None, rubric: Any = None) -> StubScore:
        return StubScore(next(it))

    return fn


# ---------------------------------------------------------------------------
# TestPredictLifts
# ---------------------------------------------------------------------------


class TestPredictLifts:
    def test_empty_recs_returns_empty(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        assert predict_lifts(b, [], rubric=RUBRIC, reference=REFERENCE,
                             run_id=RUN_ID, baseline_total=baseline_total(b), llm=DISABLED_LLM) == []

    def test_simulated_lift_matches_isolated_rescore_with_tier_short_band(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        rec = content_edit("qa_blocks")
        expected = single_rec_delta(b, rec)
        assert expected > 0  # sanity: the fix really helps

        [pred] = predict_lifts(
            b, [rec], rubric=RUBRIC, reference=REFERENCE, run_id=RUN_ID,
            baseline_total=baseline_total(b), gap=gap_with([("qa_blocks", 1, 4)]),
            llm=DISABLED_LLM,
        )
        assert pred.basis == BASIS_SIMULATED
        assert pred.point == float(expected)          # marginal == isolated for a single rec
        assert pred.high == pred.point                 # optimistic = bounded-to-target
        assert pred.low == max(0.0, pred.point - 1.0)  # one tier short (weight 1.0)
        assert pred.unit == "rubric_points"

    def test_advisory_rec_is_unknown_not_zero(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        [pred] = predict_lifts(
            b, [advisory("qa_blocks")], rubric=RUBRIC, reference=REFERENCE,
            run_id=RUN_ID, baseline_total=baseline_total(b), llm=DISABLED_LLM,
        )
        assert pred.basis == BASIS_UNKNOWN
        assert pred.point is None and pred.low is None and pred.high is None

    def test_applied_but_no_movement_is_no_deterministic_lift(self):
        # A rec whose signal changes but leaves the total flat (stub holds it constant)
        # is an honest zero — distinct from "unknown".
        b = make_bundle(schema_jsonld={"types": [], "block_count": 0})
        [pred] = predict_lifts(
            b, [schema_rec("FAQPage")], rubric=RUBRIC, reference=FakeRef(4),
            run_id=RUN_ID, baseline_total=0, llm=DISABLED_LLM,
            score_fn=stub_score_fn([0.0]),  # total never moves
        )
        assert pred.basis == BASIS_NO_LIFT
        assert pred.point == 0.0 and pred.low == 0.0 and pred.high == 0.0

    def test_schema_attribution_marginals_sum_to_aggregate_no_double_count(self):
        # Two schema recs share criterion 'schema_markup' (one tier of headroom). Their
        # per-rec marginals must sum to the combined delta — never N× the headroom.
        b = make_bundle(
            qa_blocks={"qa_pairs": [{"question": "What is X?", "answer_preview": "A."}], "pair_count": 0},
            schema_jsonld={"types": [], "block_count": 0},
        )
        recs = [schema_rec("FAQPage"), schema_rec("Article")]
        before = baseline_total(b)
        # combined: apply BOTH then score once.
        from aeo.validation.simulate import apply_recommendation
        syn = copy.deepcopy(b)
        for r in recs:
            apply_recommendation(syn, r, rubric=RUBRIC, reference=REFERENCE)
        combined = score_page(syn, RUN_ID, llm=DISABLED_LLM, rubric=RUBRIC).total - before

        preds = predict_lifts(
            b, recs, rubric=RUBRIC, reference=REFERENCE, run_id=RUN_ID,
            baseline_total=before, gap=gap_with([("schema_markup", 1, 4)]), llm=DISABLED_LLM,
        )
        points = [p.point or 0.0 for p in preds]
        assert sum(points) == float(combined)          # additive, no double-count
        assert all(pt <= combined for pt in points)     # no single rec exceeds the whole
        # A sibling that adds no further headroom is an honest no-lift, not unknown.
        for p in preds:
            assert p.basis in {BASIS_SIMULATED, BASIS_NO_LIFT}

    def test_results_align_to_input_order_while_applying_by_priority(self):
        # Input order [stats, qa]; gap ranks qa first, so qa is APPLIED first. With a stub
        # giving totals 5 then 8 from baseline 0, the first-applied (qa) earns 5 and the
        # second (stats) earns 3 — and each lands in its own INPUT slot.
        b = make_bundle(stats={"count": 0}, qa_blocks={"pair_count": 0})
        recs = [content_edit("stats_in_html"), content_edit("qa_blocks")]
        gap = gap_with([("qa_blocks", 1, 4), ("stats_in_html", 1, 4)])
        preds = predict_lifts(
            b, recs, rubric=RUBRIC, reference=FakeRef(4), run_id=RUN_ID,
            baseline_total=0, gap=gap, llm=DISABLED_LLM, score_fn=stub_score_fn([5.0, 8.0]),
        )
        assert preds[0].point == 3.0  # stats — applied second
        assert preds[1].point == 5.0  # qa — applied first (higher priority)

    def test_deterministic_repeatable(self):
        b = make_bundle(qa_blocks={"pair_count": 0}, stats={"count": 0})
        recs = [content_edit("qa_blocks"), content_edit("stats_in_html")]
        gap = gap_with([("qa_blocks", 1, 4), ("stats_in_html", 1, 4)])
        kw = dict(rubric=RUBRIC, reference=REFERENCE, run_id=RUN_ID,
                  baseline_total=baseline_total(b), gap=gap, llm=DISABLED_LLM)
        first = [p.model_dump() for p in predict_lifts(b, recs, **kw)]
        second = [p.model_dump() for p in predict_lifts(b, recs, **kw)]
        assert first == second

    def test_unknown_factory(self):
        u = PredictedLift.unknown()
        assert u.basis == BASIS_UNKNOWN and u.point is None


# ---------------------------------------------------------------------------
# TestMigration0023
# ---------------------------------------------------------------------------


class TestMigration0023:
    # 0023, not 0019: the agent-runs PR also landed a 0019, so this migration was
    # renumbered to the next free slot to avoid a schema_versions PK collision.
    def _sql(self) -> str:
        paths = {v: p for v, _n, p in migrate._discover()}
        assert "0023" in paths, "migration 0023 not discovered"
        return paths["0023"].read_text(encoding="utf-8")

    def test_adds_predicted_columns_additively(self):
        sql = self._sql()
        for col in ("predicted_delta", "predicted_low", "predicted_high", "predicted_basis"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql
        assert "ALTER TABLE recommendations" in sql

    def test_adds_detected_tier_to_outcomes(self):
        sql = self._sql()
        assert "recommendation_outcomes" in sql
        assert "ADD COLUMN IF NOT EXISTS detected_tier" in sql

    def test_is_additive(self):
        sql = self._sql().upper()
        assert "DROP TABLE" not in sql and "DROP COLUMN" not in sql
