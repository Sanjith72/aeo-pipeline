"""
Scorer-level tests — the heart of the validation.

The three fixtures are tuned to land on known tiers, so these tests double as
an executable rubric spec (v3, 10 criteria → max 50): a "strong" page scores
44/50, a "weak" page hits 18/50, and a glossary page surfaces the DefinedTerm
gap. All scoring runs with the LLM disabled, so every assertion is deterministic.
"""

from __future__ import annotations

from aeo.scoring import scorers as scorers_mod
from aeo.scoring.aggregator import score_page
from aeo.scoring.result import ScoreContext
from aeo.scoring.scorers import answer_readability, load_speed, render_accessibility, run_all
from aeo.storage.models import ExtractionBundle


def tiers(ctx: ScoreContext) -> dict[str, int]:
    return {name: cs.value for name, cs in run_all(ctx).items()}


class TestStrongPage:
    def test_every_criterion_scores_high(self, strong_ctx):
        assert tiers(strong_ctx) == {
            "schema_markup": 5,
            "qa_blocks": 5,
            "stats_in_html": 5,
            "entity_consistency": 5,
            "heading_structure": 5,
            "content_depth": 4,        # 526 words → base 3, +1 for methodology + stats
            "citation_signals": 5,
            "load_speed": 3,           # no PageSpeed in tests → neutral
            "render_accessibility": 5,  # inflation 1.03 → content already in raw HTML
            "answer_readability": 2,    # Flesch 18.9 (dense technical prose)
        }

    def test_total_and_priority(self, strong_ctx):
        ps = score_page(strong_ctx.bundle, run_id=1, llm=strong_ctx.llm, rubric=strong_ctx.rubric)
        assert ps.total == 44
        assert ps.max_possible == 50
        assert ps.priority_tier == "low"

    def test_schema_is_deterministic_not_llm(self, strong_ctx):
        cs = run_all(strong_ctx)["schema_markup"]
        assert cs.scored_by == "deterministic"
        assert cs.evidence["valued_types_present"]


class TestWeakPage:
    def test_floors_on_every_real_criterion(self, weak_ctx):
        # The eight "real content" criteria floor to 1; load_speed is neutral (3)
        # with no PSI data, and the two render/readability criteria are not
        # punitive — a sparse page can still be in-HTML and minimally readable.
        assert tiers(weak_ctx) == {
            "schema_markup": 1,
            "qa_blocks": 1,
            "stats_in_html": 1,
            "entity_consistency": 1,
            "heading_structure": 1,
            "content_depth": 1,
            "citation_signals": 1,
            "load_speed": 3,
            "render_accessibility": 5,  # inflation 0.76 → not JS-gated
            "answer_readability": 3,    # Flesch 39.6 → mid band
        }

    def test_total_is_high_priority(self, weak_ctx):
        ps = score_page(weak_ctx.bundle, run_id=1, llm=weak_ctx.llm, rubric=weak_ctx.rubric)
        assert ps.total == 18
        # 18/50 = 36% → "high" (the two non-punitive criteria lift it off the floor)
        assert ps.priority_tier == "high"

    def test_template_h1_penalty_recorded(self, weak_ctx):
        cs = run_all(weak_ctx)["heading_structure"]
        assert any(p.startswith("template_h1") for p in cs.evidence["penalties"])


class TestGlossaryPage:
    def test_defined_term_opportunity_surfaced(self, glossary_ctx):
        cs = run_all(glossary_ctx)["schema_markup"]
        assert cs.value == 1  # no schema at all
        assert cs.evidence["defined_term_opportunity"] is True
        assert "DefinedTerm" in cs.notes

    def test_entity_consistency_uses_undefined_ratio_branch(self, glossary_ctx):
        # Entity named once, zero first-person → ratio is None → top tier.
        cs = run_all(glossary_ctx)["entity_consistency"]
        assert cs.value == 5


class TestLoadSpeedScoring:
    """load_speed is the only criterion driven by injected PageSpeed data."""

    def _ctx(self, rubric, disabled_llm, *, perf, js_only=False) -> ScoreContext:
        bundle = ExtractionBundle(page_id=1)
        bundle.put("render_mode", {"js_only_content": js_only, "inflation_ratio": 1.0})
        if perf is not None:
            bundle.put("pagespeed", {"performance_score": perf, "lcp_ms": 1200, "tbt_ms": 50, "cls": 0.02})
        return ScoreContext(bundle=bundle, rubric=rubric, llm=disabled_llm)

    def test_psi_score_maps_to_tier(self, rubric, disabled_llm):
        mapping = {95: 5, 90: 5, 80: 4, 75: 4, 60: 3, 50: 3, 30: 2, 20: 1, 0: 1}
        for perf, expected in mapping.items():
            ctx = self._ctx(rubric, disabled_llm, perf=perf)
            assert load_speed.score(ctx).value == expected, f"perf={perf}"

    def test_missing_psi_is_neutral(self, rubric, disabled_llm):
        ctx = self._ctx(rubric, disabled_llm, perf=None)
        cs = load_speed.score(ctx)
        assert cs.value == 3
        assert cs.scored_by == "deterministic"
        assert cs.evidence["psi_available"] is False

    def test_js_only_content_penalty(self, rubric, disabled_llm):
        ctx = self._ctx(rubric, disabled_llm, perf=95, js_only=True)
        assert load_speed.score(ctx).value == 4  # 5 - 1 penalty


class TestRenderAccessibilityScoring:
    """Criterion 9 — wraps the render_mode extractor; lower inflation is better."""

    def _ctx(self, rubric, disabled_llm, render: dict | None) -> ScoreContext:
        bundle = ExtractionBundle(page_id=1)
        if render is not None:
            bundle.put("render_mode", render)
        return ScoreContext(bundle=bundle, rubric=rubric, llm=disabled_llm)

    def test_inflation_maps_to_tier(self, rubric, disabled_llm):
        # default inflation_max = {5: 1.5, 4: 2.5, 3: 4.0, 2: 8.0}; above 8.0 → 1
        mapping = {1.0: 5, 1.5: 5, 2.0: 4, 2.5: 4, 3.0: 3, 6.0: 2, 9.0: 1}
        for inflation, expected in mapping.items():
            ctx = self._ctx(rubric, disabled_llm, {"inflation_ratio": inflation})
            assert render_accessibility.score(ctx).value == expected, f"inflation={inflation}"

    def test_js_only_content_floors_to_one(self, rubric, disabled_llm):
        ctx = self._ctx(rubric, disabled_llm, {"js_only_content": True, "inflation_ratio": 1.0})
        cs = render_accessibility.score(ctx)
        assert cs.value == 1
        assert "js_only_content" in cs.notes

    def test_missing_render_data_is_neutral(self, rubric, disabled_llm):
        ctx = self._ctx(rubric, disabled_llm, None)
        cs = render_accessibility.score(ctx)
        assert cs.value == 3
        assert cs.evidence["render_data"] is False
        assert cs.scored_by == "deterministic"


class TestAnswerReadabilityScoring:
    """Criterion 10 — wraps readability + chunker; rewards quotable content."""

    def _ctx(self, rubric, disabled_llm, *, read: dict, chunk: dict | None = None) -> ScoreContext:
        bundle = ExtractionBundle(page_id=1)
        bundle.put("readability", read)
        bundle.put("chunker", chunk or {})
        return ScoreContext(bundle=bundle, rubric=rubric, llm=disabled_llm)

    def test_flesch_maps_to_base_tier(self, rubric, disabled_llm):
        # default flesch_tiers = {1: 0, 2: 20, 3: 30, 4: 45, 5: 55}; 2 chunks → no adj
        mapping = {10.0: 1, 25.0: 2, 35.0: 3, 50.0: 4, 60.0: 5}
        for flesch, expected in mapping.items():
            ctx = self._ctx(
                rubric, disabled_llm,
                read={"word_count": 300, "flesch_reading_ease": flesch, "avg_sentence_length": 15},
                chunk={"chunk_count": 2},
            )
            assert answer_readability.score(ctx).value == expected, f"flesch={flesch}"

    def test_long_sentences_dock_a_point(self, rubric, disabled_llm):
        ctx = self._ctx(
            rubric, disabled_llm,
            read={"word_count": 300, "flesch_reading_ease": 60, "avg_sentence_length": 40},
            chunk={"chunk_count": 2},
        )
        # base 5, -1 for long sentences (40 > 28 max)
        assert answer_readability.score(ctx).value == 4

    def test_segmentation_gives_credit(self, rubric, disabled_llm):
        ctx = self._ctx(
            rubric, disabled_llm,
            read={"word_count": 300, "flesch_reading_ease": 35, "avg_sentence_length": 15},
            chunk={"chunk_count": 5},
        )
        # base 3, +1 for ≥3 passages
        assert answer_readability.score(ctx).value == 4

    def test_monolithic_block_penalised(self, rubric, disabled_llm):
        ctx = self._ctx(
            rubric, disabled_llm,
            read={"word_count": 300, "flesch_reading_ease": 35, "avg_sentence_length": 15},
            chunk={"chunk_count": 1},
        )
        # base 3, -1 for a single monolithic block
        assert answer_readability.score(ctx).value == 2

    def test_insufficient_text_floors_to_one(self, rubric, disabled_llm):
        ctx = self._ctx(
            rubric, disabled_llm,
            read={"word_count": 10, "flesch_reading_ease": 80},
            chunk={"chunk_count": 0},
        )
        cs = answer_readability.score(ctx)
        assert cs.value == 1
        assert cs.evidence["reason"] == "insufficient text to assess"


class TestRunAllIsolation:
    def test_one_failing_scorer_does_not_abort_the_rest(self, strong_ctx, monkeypatch):
        def boom(ctx):
            raise RuntimeError("kaboom")

        monkeypatch.setitem(scorers_mod.SCORERS, "schema_markup", boom)
        results = scorers_mod.run_all(strong_ctx)

        failed = results["schema_markup"]
        assert failed.value == strong_ctx.rubric.scale_min
        assert failed.scored_by == "error"
        assert "kaboom" in failed.evidence["error"]
        # The other nine scorers still produced real scores.
        assert results["qa_blocks"].value == 5
        assert len(results) == 10
