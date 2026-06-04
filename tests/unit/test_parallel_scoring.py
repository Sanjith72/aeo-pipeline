"""
Unit tests for the v4 Parallel Processor.

The contract is *identical output to sequential* — parallelism is a performance
change, never a behavior change. We assert byte-for-byte parity across the three
engineered fixtures and verify per-scorer failure isolation still floors a broken
scorer (not aborts the page) and preserves the fixed 10-key order.
"""

from __future__ import annotations

from conftest import build_bundle, load_fixture

from aeo.nlp.llm import LLMClient
from aeo.scoring import score_page
from aeo.scoring.result import ScoreContext
from aeo.scoring.rubric import load_rubric
from aeo.scoring.scorers import SCORERS, run_all, run_all_parallel
from aeo.settings import LLMCfg

RUBRIC = load_rubric()
DISABLED_LLM = LLMClient(LLMCfg(enabled=False))

FIXTURES = [
    ("strong_page.html", "https://securin.io/blog/vulnerability-management-guide"),
    ("weak_page.html", "https://securin.io/resources"),
    ("glossary_page.html", "https://securin.io/glossary"),
]


def _ctx(name: str, url: str) -> ScoreContext:
    bundle = build_bundle(load_fixture(name), url)
    return ScoreContext(bundle=bundle, rubric=RUBRIC, llm=DISABLED_LLM)


def _comparable(scores):
    return {n: (c.value, c.scored_by, c.notes) for n, c in scores.items()}


class TestParity:
    def test_run_all_parallel_matches_sequential(self):
        for name, url in FIXTURES:
            ctx = _ctx(name, url)
            assert _comparable(run_all_parallel(ctx)) == _comparable(run_all(ctx))

    def test_parallel_preserves_key_order(self):
        ctx = _ctx(*FIXTURES[0])
        assert list(run_all_parallel(ctx).keys()) == list(SCORERS.keys())

    def test_score_page_parallel_matches_sequential_total(self):
        for name, url in FIXTURES:
            bundle = build_bundle(load_fixture(name), url)
            seq = score_page(bundle, 1, llm=DISABLED_LLM, rubric=RUBRIC, parallel=False)
            par = score_page(bundle, 1, llm=DISABLED_LLM, rubric=RUBRIC, parallel=True)
            assert par.total == seq.total
            assert par.max_possible == seq.max_possible
            assert _comparable(par.criteria) == _comparable(seq.criteria)


class TestFailureIsolation:
    def test_broken_scorer_is_floored_not_raised(self, monkeypatch):
        def boom(_ctx):
            raise RuntimeError("scorer exploded")

        monkeypatch.setitem(SCORERS, "qa_blocks", boom)
        ctx = _ctx(*FIXTURES[0])
        result = run_all_parallel(ctx)
        assert set(result.keys()) == set(SCORERS.keys())  # contract holds
        assert result["qa_blocks"].scored_by == "error"
        assert result["qa_blocks"].value == RUBRIC.scale_min

    def test_single_worker_still_correct(self):
        ctx = _ctx(*FIXTURES[0])
        assert _comparable(run_all_parallel(ctx, max_workers=1)) == _comparable(run_all(ctx))
