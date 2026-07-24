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
