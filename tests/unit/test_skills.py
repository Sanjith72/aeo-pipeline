"""v5 CH-04/CH-06 — the five-skill derived scoring layer."""

from __future__ import annotations

from aeo.scoring.skills import SKILL_KEYS, build_skill_scores
from aeo.storage.models import CriterionScore, ExtractionBundle, PageScore


def _page_score(tiers: dict[str, int]) -> PageScore:
    crit = {n: CriterionScore(name=n, value=v) for n, v in tiers.items()}
    return PageScore(page_id=1, run_id=1, criteria=crit, total=sum(tiers.values()),
                     max_possible=len(tiers) * 5, priority_tier="high")


_ALL_CRITERIA = {
    "schema_markup": 3, "qa_blocks": 3, "heading_structure": 3, "entity_consistency": 3,
    "citation_signals": 3, "stats_in_html": 3, "answer_readability": 3, "load_speed": 3,
    "render_accessibility": 3, "content_depth": 3,
}


def _bundle(*, strong: bool) -> ExtractionBundle:
    b = ExtractionBundle(page_id=1)
    if strong:
        b.put("meta", {"title": "Family dentistry in Duluth — gentle care", "description":
                       "Gentle family dentistry: cleanings, implants, and emergency care for Duluth families."})
        b.put("headings", {"h1_text": "Family dentistry Duluth trusts", "h1_count": 1,
                           "template_h1": False, "by_level": {"h2": ["Our services"]}})
        b.put("links", {"internal": ["https://x.com/contact", "https://x.com/pricing",
                        "https://x.com/case-studies", "https://x.com/about", "https://x.com/blog"],
                        "internal_count": 5})
    else:
        b.put("meta", {"title": "Home", "description": ""})
        b.put("headings", {"h1_text": "Welcome", "h1_count": 2, "template_h1": True, "by_level": {"h2": []}})
        b.put("links", {"internal": ["https://x.com/blog"], "internal_count": 1})
    b.put("chunker", {"chunks": [{"text": "We help businesses."}]})
    return b


def test_every_page_gets_five_skills_0_100() -> None:
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    assert set(out["skills"].keys()) == set(SKILL_KEYS)
    for skill in out["skills"].values():
        assert 0 <= skill["score"] <= 100
        assert len(skill["suggestions"]) >= 1 or skill["score"] >= 100 or skill["confidence"] == "neutral"


def test_messaging_conversion_provisional_without_llm() -> None:
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    assert out["skills"]["messaging"]["confidence"] == "provisional"
    assert out["skills"]["conversion"]["confidence"] == "provisional"


def test_case_studies_link_credits_conversion_midfunnel() -> None:
    # Regression: the /case-studies path must register as a mid-funnel signal.
    strong = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    signals = strong["skills"]["conversion"]["evidence"]["signals"]
    assert signals["mid_funnel_path"] is True


def test_weighted_overall_favours_high_weight_skills() -> None:
    # Messaging/Conversion (weight 1.4) failing drags the weighted overall BELOW the plain mean.
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=False))
    plain = round(sum(s["score"] for s in out["skills"].values()) / 5)
    assert out["overall"] <= plain  # messaging/conversion = 0 here, and they're the heaviest


def test_priorities_rank_high_weight_failures_first() -> None:
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=False))
    assert out["priorities"], "a weak page must surface fixes"
    top = out["priorities"][0]
    assert top["skill"] in ("messaging", "conversion")  # the heaviest failing skills
    # impact is monotonically non-increasing (sorted)
    impacts = [p["impact"] for p in out["priorities"]]
    assert impacts == sorted(impacts, reverse=True)


class _FakeLLM:
    enabled = True

    def __init__(self, payload):
        self._payload = payload

    def generate_json(self, prompt):
        return self._payload


def test_llm_makes_messaging_hybrid_with_page_specific_suggestions() -> None:
    llm = _FakeLLM({"score": 40, "suggestions": ["Rewrite the H1 to name your service."]})
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True), llm=llm)
    m = out["skills"]["messaging"]
    assert m["confidence"] == "hybrid"
    assert m["suggestions"][0]["text"] == "Rewrite the H1 to name your service."


def test_llm_failure_falls_back_to_deterministic() -> None:
    class _Broken:
        enabled = True

        def generate_json(self, prompt):
            raise RuntimeError("provider down")

    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True), llm=_Broken())
    # never blocks or floors — stays provisional (deterministic), never confidence 'error'
    assert out["skills"]["messaging"]["confidence"] == "provisional"


def test_malformed_llm_output_falls_back() -> None:
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True),
                             llm=_FakeLLM({"nonsense": True}))
    assert out["skills"]["conversion"]["confidence"] == "provisional"


# ── CH-14: AI-snapshot visibility folded into Discovery & Visibility ───────────────


def _discovery(out) -> dict:
    return out["skills"]["discovery_visibility"]


def test_ai_visibility_absent_leaves_discovery_untouched() -> None:
    base = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    with_none = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True), ai_visibility=None)
    assert _discovery(base)["score"] == _discovery(with_none)["score"]
    assert _discovery(with_none)["ai_visibility"] is None


def test_unavailable_verdict_never_penalises() -> None:
    """The default deployment has Perplexity off -> every page is 'unavailable'. Penalising
    a check that never ran would invent a failure and would silently move every score the
    moment ops enables the engine."""
    base = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    out = build_skill_scores(
        _page_score(_ALL_CRITERIA), _bundle(strong=True),
        ai_visibility={"status": "unavailable", "reason": "not_configured"},
    )
    assert _discovery(out)["score"] == _discovery(base)["score"]
    assert _discovery(out)["ai_visibility"]["status"] == "unavailable"  # still attached


def test_cited_verdict_gives_no_fake_boost() -> None:
    base = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    out = build_skill_scores(
        _page_score(_ALL_CRITERIA), _bundle(strong=True),
        ai_visibility={"status": "cited", "via": "citations"},
    )
    assert _discovery(out)["score"] == _discovery(base)["score"]


def test_not_cited_penalises_and_suggests_first() -> None:
    base = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=True))
    out = build_skill_scores(
        _page_score(_ALL_CRITERIA), _bundle(strong=True),
        ai_visibility={"status": "not_cited", "via": None},
    )
    d = _discovery(out)
    assert d["score"] < _discovery(base)["score"]
    assert d["suggestions"][0]["criterion"] == "ai_visibility"
    assert len(d["suggestions"]) <= 3


def test_not_cited_score_never_goes_negative() -> None:
    floor = {k: 1 for k in _ALL_CRITERIA}
    out = build_skill_scores(
        _page_score(floor), _bundle(strong=False),
        ai_visibility={"status": "not_cited"},
    )
    assert _discovery(out)["score"] >= 0


# ── CH-06: predicted lift is the third ranking factor ──────────────────────────────


def test_priorities_carry_a_lift_factor_and_basis() -> None:
    out = build_skill_scores(_page_score(_ALL_CRITERIA), _bundle(strong=False))
    assert out["priorities"], "expected ranked fixes on a weak page"
    for p in out["priorities"]:
        assert 0.0 <= p["lift"] <= 1.0
        assert p["lift_basis"] in ("headroom", "imputed")
        # criterion-backed items must be MEASURED, never imputed
        if p["criterion"] and p["criterion"] != "ai_visibility":
            assert p["lift_basis"] == "headroom"


def test_lift_reorders_within_a_skill() -> None:
    """Two Discovery criteria, same skill weight and severity: the one with more headroom
    (lower tier) must rank first. Under weight x severity alone they were tied, so this is
    the ONLY guard that predicted lift actually reaches the ranking.

    Tier choice matters: _mapped_skill emits suggestions for just the 3 WEAKEST criteria, so
    both compared criteria must survive that cut. qa_blocks is parked at 5 to make it the one
    that is dropped. The presence assertions below keep the test from silently going vacuous
    again if that selection ever changes."""
    tiers = {**_ALL_CRITERIA, "schema_markup": 1, "entity_consistency": 2,
             "heading_structure": 3, "qa_blocks": 5}
    out = build_skill_scores(_page_score(tiers), _bundle(strong=True))
    by_crit = {p["criterion"]: p for p in out["priorities"] if p["skill"] == "discovery_visibility"}

    assert "schema_markup" in by_crit, f"test went vacuous — ranked: {sorted(by_crit)}"
    assert "entity_consistency" in by_crit, f"test went vacuous — ranked: {sorted(by_crit)}"
    # Same skill => identical weight and severity; only lift can separate them.
    assert by_crit["schema_markup"]["lift"] > by_crit["entity_consistency"]["lift"]
    assert by_crit["schema_markup"]["impact"] > by_crit["entity_consistency"]["impact"]
    order = [p["criterion"] for p in out["priorities"] if p["skill"] == "discovery_visibility"]
    assert order.index("schema_markup") < order.index("entity_consistency")


def test_zero_headroom_criterion_ranks_last() -> None:
    """A criterion already at tier 5 has no headroom -> lift 0 -> it cannot outrank a real
    failure, even inside a low-scoring skill."""
    from aeo.scoring.skills import _lift_factor

    skill = {"evidence": {"tier_inputs": {"schema_markup": 5, "qa_blocks": 1}}}
    assert _lift_factor({"criterion": "schema_markup"}, skill) == 0.0
    assert _lift_factor({"criterion": "qa_blocks"}, skill) == 1.0
    assert _lift_factor({"criterion": None}, skill) is None  # LLM suggestion -> imputed
