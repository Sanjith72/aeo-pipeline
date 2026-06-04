"""
Unit tests for the validated-wins feedback loop (v4), offline.

The proposal logic is a spec for *controlled* learning: it nudges a criterion's
target only when the cited cohort consistently and materially out-tiers both the
current target and the non-cited cohort, refuses to act on a thin sample, never
exceeds the rubric scale, and always returns human-gated 'proposed' rows.
"""

from __future__ import annotations

from aeo.reference import load_reference
from aeo.reference.feedback import CitationObservation, propose_criteria_refinements
from aeo.scoring.rubric import load_rubric

REFERENCE = load_reference()
RUBRIC = load_rubric()


def obs(page_id, cited, **tiers):
    return CitationObservation(page_id=page_id, url=f"https://x.io/{page_id}", cited=cited, tiers=tiers)


class TestProposals:
    def test_proposes_raise_when_cited_cohort_outperforms(self):
        # current target for stats_in_html is 4. Cited pages sit at 5, non-cited at 3.
        cited = [obs(i, True, stats_in_html=5) for i in range(4)]
        uncited = [obs(100 + i, False, stats_in_html=3) for i in range(4)]
        props = propose_criteria_refinements(cited + uncited, reference=REFERENCE, rubric=RUBRIC)
        stats = [p for p in props if p.criterion == "stats_in_html"]
        assert stats, "expected a refinement for stats_in_html"
        p = stats[0]
        assert p.current_target == 4
        assert p.proposed_target == 5
        assert p.status == "proposed"
        assert p.evidence["cited_n"] == 4

    def test_thin_sample_proposes_nothing(self):
        cited = [obs(i, True, stats_in_html=5) for i in range(2)]  # below MIN_CITED_SAMPLE
        assert propose_criteria_refinements(cited, reference=REFERENCE, rubric=RUBRIC) == []

    def test_no_proposal_when_cited_not_better_than_uncited(self):
        # Both cohorts at 5 → no margin → no proposal even though > target.
        cited = [obs(i, True, stats_in_html=5) for i in range(4)]
        uncited = [obs(100 + i, False, stats_in_html=5) for i in range(4)]
        props = propose_criteria_refinements(cited + uncited, reference=REFERENCE, rubric=RUBRIC)
        assert [p for p in props if p.criterion == "stats_in_html"] == []

    def test_no_proposal_when_cited_at_or_below_target(self):
        # Cited median == current target (4) → not "consistently exceeds" → skip.
        cited = [obs(i, True, stats_in_html=4) for i in range(4)]
        uncited = [obs(100 + i, False, stats_in_html=2) for i in range(4)]
        props = propose_criteria_refinements(cited + uncited, reference=REFERENCE, rubric=RUBRIC)
        assert [p for p in props if p.criterion == "stats_in_html"] == []

    def test_proposal_never_exceeds_scale_max(self):
        cited = [obs(i, True, render_accessibility=5) for i in range(4)]  # target already 5
        uncited = [obs(100 + i, False, render_accessibility=2) for i in range(4)]
        props = propose_criteria_refinements(cited + uncited, reference=REFERENCE, rubric=RUBRIC)
        for p in props:
            assert p.proposed_target <= RUBRIC.scale_max
        # render_accessibility target is already 5 → cannot raise → no proposal
        assert [p for p in props if p.criterion == "render_accessibility"] == []
