"""
Dual-Layer Gap Analysis tests.

All values are hand-computable: targets, weights and competitor tiers are
injected (not read from config) so each normalized gap can be checked against a
worked-out fraction. Pure functions — no DB, no LLM.
"""

from __future__ import annotations

import pytest

from aeo.processor.gap_analysis import (
    CompetitorPage,
    analyze_gap,
    persist_gap,
    select_competitor,
)
from aeo.reference import Reference
from aeo.reference.query_intent import QueryIntentCfg
from aeo.scoring.rubric import Criterion, Rubric
from aeo.storage.models import CriterionScore, PageScore


def make_score(tiers: dict[str, int], *, page_id: int = 1, run_id: int = 1) -> PageScore:
    criteria = {n: CriterionScore(name=n, value=v) for n, v in tiers.items()}
    return PageScore(
        page_id=page_id,
        run_id=run_id,
        criteria=criteria,
        total=sum(tiers.values()),
        max_possible=5 * len(tiers),
        priority_tier="x",
        rubric_version="2.0",
    )


def make_reference(targets: dict[str, int]) -> Reference:
    # Only target_for is exercised; architecture/intent are unused here.
    return Reference(targets=targets, architecture={}, intent=QueryIntentCfg())


def make_rubric(names, *, weights: dict[str, float] | None = None) -> Rubric:
    weights = weights or {}
    criteria = {
        n: Criterion(name=n, label=n, weight=weights.get(n, 1.0), cfg={}) for n in names
    }
    return Rubric(criteria=criteria, scale_min=1, scale_max=5)


class TestBestPracticeOnly:
    def test_meeting_every_target_is_zero_gap(self):
        score = make_score({"a": 5, "b": 5})
        ref = make_reference({"a": 4, "b": 4})
        rubric = make_rubric(["a", "b"])

        res = analyze_gap(score, reference=ref, rubric=rubric)

        assert res.bestpractice_gap == 0.0
        assert res.competitor_gap is None
        assert res.overall_gap == 0.0
        assert res.criterion_gaps == []  # nothing to fix

    def test_normalized_gap_matches_worked_fraction(self):
        # a: 4-2=2, b: 5-1=4 → weighted 6; max (4-1)+(5-1)=7 → 6/7
        score = make_score({"a": 2, "b": 1})
        ref = make_reference({"a": 4, "b": 5})
        rubric = make_rubric(["a", "b"])

        res = analyze_gap(score, reference=ref, rubric=rubric)

        assert res.bestpractice_gap == pytest.approx(6 / 7, abs=1e-3)
        assert res.competitor_gap is None
        assert res.overall_gap == res.bestpractice_gap  # falls back 100% to best-practice

    def test_deficiency_list_ordered_by_weighted_priority(self):
        score = make_score({"a": 2, "b": 1})
        ref = make_reference({"a": 4, "b": 5})
        rubric = make_rubric(["a", "b"])

        res = analyze_gap(score, reference=ref, rubric=rubric)

        # b's shortfall (4 pts) outranks a's (2 pts)
        assert [g.criterion for g in res.criterion_gaps] == ["b", "a"]
        assert res.criterion_gaps[0].bestpractice_gap == 4
        assert res.criterion_gaps[1].bestpractice_gap == 2

    def test_weight_can_reorder_the_list(self):
        # Equal raw shortfalls, but 'a' is weighted heavier → ranks first.
        score = make_score({"a": 2, "b": 2})
        ref = make_reference({"a": 4, "b": 4})
        rubric = make_rubric(["a", "b"], weights={"a": 3.0, "b": 1.0})

        res = analyze_gap(score, reference=ref, rubric=rubric)

        assert [g.criterion for g in res.criterion_gaps] == ["a", "b"]


class TestWithCompetitor:
    def test_overall_blends_60_40(self):
        # bp: each 3-2=1 → 2/4 = 0.5 ; comp: each 5-2=3 → 6/8 = 0.75
        score = make_score({"a": 2, "b": 2})
        ref = make_reference({"a": 3, "b": 3})
        rubric = make_rubric(["a", "b"])
        comp = CompetitorPage(page_id=99, intent="informational", total=10,
                              tiers={"a": 5, "b": 5})

        res = analyze_gap(score, reference=ref, rubric=rubric, competitor=comp,
                          intent="informational")

        assert res.bestpractice_gap == pytest.approx(0.5, abs=1e-3)
        assert res.competitor_gap == pytest.approx(0.75, abs=1e-3)
        # 0.6*0.5 + 0.4*0.75 = 0.6
        assert res.overall_gap == pytest.approx(0.6, abs=1e-3)
        assert res.competitor_page_id == 99
        assert res.intent == "informational"

    def test_criterion_records_competitor_tier(self):
        score = make_score({"a": 2})
        ref = make_reference({"a": 3})
        rubric = make_rubric(["a"])
        comp = CompetitorPage(page_id=7, intent="x", total=5, tiers={"a": 5})

        res = analyze_gap(score, reference=ref, rubric=rubric, competitor=comp)

        row = res.criterion_gaps[0]
        assert row.competitor == 5
        assert row.competitor_gap == 3  # 5 - 2

    def test_competitor_missing_a_criterion_is_skipped_in_that_layer(self):
        # competitor only covers 'a'; 'b' contributes to best-practice only.
        score = make_score({"a": 2, "b": 2})
        ref = make_reference({"a": 3, "b": 3})
        rubric = make_rubric(["a", "b"])
        comp = CompetitorPage(page_id=1, intent="x", total=5, tiers={"a": 5})

        res = analyze_gap(score, reference=ref, rubric=rubric, competitor=comp)

        # comp layer: only a → 3/4 = 0.75
        assert res.competitor_gap == pytest.approx(0.75, abs=1e-3)
        b_row = next(g for g in res.criterion_gaps if g.criterion == "b")
        assert b_row.competitor is None
        assert b_row.competitor_gap == 0


class TestSelectCompetitor:
    def _cands(self):
        return [
            CompetitorPage(page_id=1, intent="informational", total=30, tiers={}),
            CompetitorPage(page_id=2, intent="informational", total=40, tiers={}),
            CompetitorPage(page_id=3, intent="commercial", total=50, tiers={}),
        ]

    def test_picks_highest_total_matching_intent(self):
        best = select_competitor(self._cands(), "informational")
        assert best is not None and best.page_id == 2  # 40 > 30, commercial ignored

    def test_no_match_returns_none(self):
        assert select_competitor(self._cands(), "navigational") is None

    def test_none_intent_returns_none(self):
        assert select_competitor(self._cands(), None) is None


class TestDetailAndPersist:
    def test_detail_flags_competitor_availability(self):
        score = make_score({"a": 2})
        ref = make_reference({"a": 4})
        rubric = make_rubric(["a"])

        res = analyze_gap(score, reference=ref, rubric=rubric, intent="informational")
        detail = res.to_detail()

        assert detail["competitor_available"] is False
        assert detail["intent"] == "informational"
        assert detail["weights"] == {"bestpractice": 0.6, "competitor": 0.4}
        assert detail["criterion_gaps"][0]["criterion"] == "a"

    def test_persist_stores_zero_for_absent_competitor(self, monkeypatch):
        captured = {}

        def fake_put(**kwargs):
            captured.update(kwargs)
            return 123

        from aeo.storage.repos import gaps as gaps_repo

        monkeypatch.setattr(gaps_repo, "put", fake_put)

        score = make_score({"a": 2})
        ref = make_reference({"a": 4})
        rubric = make_rubric(["a"])
        res = analyze_gap(score, reference=ref, rubric=rubric)

        rec_id = persist_gap(res)

        assert rec_id == 123
        assert captured["competitor_gap"] == 0.0  # None coerced to 0 for the NOT NULL column
        assert captured["detail"]["competitor_available"] is False
