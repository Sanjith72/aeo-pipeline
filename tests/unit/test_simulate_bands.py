"""
Drift guard for validation/simulate.py.

``simulate.py`` hard-codes two tier-band tables (``_QA_PAIRS_FOR_TIER`` and
``_DEPTH_WORDS_FOR_TIER``) that mirror band logic living *inside* the scorers
(qa_blocks / content_depth), because those bands aren't in config. A file comment
warns "keep in sync if those bands ever change" — this test enforces it: for each
band, building a bundle at the band's value must make the real scorer reach (at
least) the target tier. If a scorer's bands move, this fails instead of letting
the synthetic re-score quietly lie about whether an edit helped.
"""

from __future__ import annotations

from aeo.scoring.result import ScoreContext
from aeo.scoring.scorers import content_depth, qa_blocks
from aeo.storage.models import ExtractionBundle
from aeo.validation.simulate import _DEPTH_WORDS_FOR_TIER, _QA_PAIRS_FOR_TIER


def _ctx(rubric, disabled_llm, sections: dict[str, dict]) -> ScoreContext:
    bundle = ExtractionBundle(page_id=1)
    for name, payload in sections.items():
        bundle.put(name, payload)
    return ScoreContext(bundle=bundle, rubric=rubric, llm=disabled_llm)


def test_qa_pair_bands_reach_their_target_tier(rubric, disabled_llm):
    for tier, pairs in _QA_PAIRS_FOR_TIER.items():
        ctx = _ctx(rubric, disabled_llm, {
            "qa_blocks": {"pair_count": pairs},
            "schema_jsonld": {"types": []},  # no FAQ bonus — test the base bands
        })
        got = qa_blocks.score(ctx).value
        assert got >= tier, f"{pairs} Q&A pairs scored tier {got}, expected ≥ {tier}"


def test_depth_word_bands_reach_their_target_tier(rubric, disabled_llm):
    for tier, words in _DEPTH_WORDS_FOR_TIER.items():
        ctx = _ctx(rubric, disabled_llm, {"readability": {"word_count": words}})
        got = content_depth.score(ctx).value
        assert got >= tier, f"{words} words scored tier {got}, expected ≥ {tier}"
