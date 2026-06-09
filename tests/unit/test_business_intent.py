"""Business-Model / Intent Engine — deterministic scoring + optional LLM tiebreak."""

from __future__ import annotations

from typing import Any

from aeo.intelligence.business_intent import BusinessModel, detect_business_model
from aeo.intelligence.classification import SiteClass
from aeo.intelligence.signals import page_view


def _pages(*paths: str, page_type: str = "default") -> list:
    return [page_view(f"https://x.com{p}", page_type) for p in paths]


class _FakeLLM:
    """Minimal LLMClient stand-in: enabled + generate_json returning a canned dict."""

    def __init__(self, payload: Any, *, enabled: bool = True) -> None:
        self._payload = payload
        self.enabled = enabled
        self.model = "fake"

    def generate_json(self, prompt: str, system: str | None = None) -> Any:
        return self._payload


def test_ecommerce_signals() -> None:
    intent = detect_business_model(_pages("/cart", "/checkout", "/shop"), site_class=SiteClass.SMALL)
    assert intent.model is BusinessModel.ECOMMERCE
    assert intent.decided_by == "deterministic"
    assert intent.confidence > 0


def test_saas_signals() -> None:
    intent = detect_business_model(_pages("/pricing", "/demo", "/signup"), site_class=SiteClass.SMALL)
    assert intent.model is BusinessModel.SAAS


def test_local_signals() -> None:
    intent = detect_business_model(_pages("/locations", "/appointment", "/hours"), site_class=SiteClass.SMALL)
    assert intent.model is BusinessModel.LOCAL


def test_agency_signals() -> None:
    intent = detect_business_model(_pages("/case-studies", "/portfolio", "/our-work"), site_class=SiteClass.SMALL)
    assert intent.model is BusinessModel.AGENCY


def test_publisher_from_blog_ratio() -> None:
    pages = [page_view(f"https://x.com/blog/{i}", "blog") for i in range(8)]
    pages.append(page_view("https://x.com/about", "about"))
    intent = detect_business_model(pages, site_class=SiteClass.MEDIUM)
    assert intent.model is BusinessModel.PUBLISHER


def test_no_signal_defaults_to_lead_gen() -> None:
    intent = detect_business_model(_pages("/", "/team"), site_class=SiteClass.SMALL)
    assert intent.model is BusinessModel.LEAD_GEN
    assert intent.decided_by == "default"
    assert intent.confidence == 0.0


def test_industry_hint_boost() -> None:
    # Neutral paths, but the onboarding topic text tips it to ecommerce.
    intent = detect_business_model(
        _pages("/", "/team"), site_class=SiteClass.SMALL, topic="online retail store"
    )
    assert intent.model is BusinessModel.ECOMMERCE
    assert any("industry:" in e for e in intent.evidence)


def test_llm_tiebreak_chosen_when_models_tie() -> None:
    # /product -> ecommerce(1); /features -> saas(1): a tie within the margin.
    pages = _pages("/product", "/features")
    llm = _FakeLLM({"model": "saas"}, enabled=True)
    intent = detect_business_model(pages, site_class=SiteClass.SMALL, llm=llm)
    assert intent.model is BusinessModel.SAAS
    assert intent.decided_by == "llm-tiebreak"
    assert "llm-tiebreak" in intent.evidence


def test_tie_without_llm_is_deterministic() -> None:
    pages = _pages("/product", "/features")
    intent = detect_business_model(pages, site_class=SiteClass.SMALL, llm=None)
    # deterministic tie-break is by label order: 'ecommerce' < 'saas'
    assert intent.model is BusinessModel.ECOMMERCE
    assert intent.decided_by == "deterministic"


def test_llm_invalid_choice_falls_back_to_deterministic() -> None:
    pages = _pages("/product", "/features")
    llm = _FakeLLM({"model": "not-a-real-model"}, enabled=True)
    intent = detect_business_model(pages, site_class=SiteClass.SMALL, llm=llm)
    assert intent.model is BusinessModel.ECOMMERCE
    assert intent.decided_by == "deterministic"


def test_disabled_llm_never_engages_tiebreak() -> None:
    pages = _pages("/product", "/features")
    llm = _FakeLLM({"model": "saas"}, enabled=False)
    intent = detect_business_model(pages, site_class=SiteClass.SMALL, llm=llm)
    assert intent.decided_by == "deterministic"
