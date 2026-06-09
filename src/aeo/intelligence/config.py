"""
Intelligence-layer configuration (``config/intelligence.yaml``).

Mirrors ``crawl.prioritize.load_prioritization_cfg``: an ``lru_cache``d dataclass
loaded from YAML over code defaults, so tier thresholds, business-model signal
weights, journey-stage maps, and scenario routing are tunable without a code change
— and the engines still work if the YAML is absent. Treat the result as read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..settings import load_yaml_file

# ── code defaults (the YAML overrides these section-by-section) ───────────────

# Upper bound (inclusive) of each tier by discovered page count; > large = enterprise.
_DEFAULT_THRESHOLDS: dict[str, int] = {"single_page": 1, "small": 10, "medium": 50, "large": 200}

# A page is an archetype when its page_type is in `page_types` OR its path matches
# one of `slug_tokens` (token_hit semantics). page_type values mirror the prioritizer.
_DEFAULT_ARCHETYPES: dict[str, dict[str, list[str]]] = {
    "about":        {"page_types": ["about"],             "slug_tokens": ["about", "about-us", "team", "company", "who-we-are"]},
    "contact":      {"page_types": ["contact"],           "slug_tokens": ["contact", "contact-us", "get-in-touch"]},
    "services":     {"page_types": ["solution", "product"], "slug_tokens": ["service", "services", "solution", "solutions", "what-we-do"]},
    "industries":   {"page_types": [],                    "slug_tokens": ["industr", "sector", "sectors", "vertical", "industries"]},
    "faq":          {"page_types": [],                    "slug_tokens": ["faq", "faqs", "questions", "help-center"]},
    "resources":    {"page_types": ["blog", "pillar"],    "slug_tokens": ["resource", "resources", "guide", "guides", "learn", "knowledge"]},
    "case_studies": {"page_types": [],                    "slug_tokens": ["case-stud", "case-study", "customer", "customers", "success-stor", "our-work"]},
    "blog":         {"page_types": ["blog"],              "slug_tokens": ["blog", "article", "articles", "news", "insights", "post"]},
    "product":      {"page_types": ["product"],           "slug_tokens": ["product", "products", "platform", "pricing", "features"]},
}

# Archetypes expected per business model — the denominator of structure_score, so a
# SaaS site isn't dinged for lacking "industries" while a single-pager is flagged for
# lacking everything.
_DEFAULT_EXPECTED: dict[str, list[str]] = {
    "lead_gen":   ["about", "contact", "services", "industries", "faq", "resources", "case_studies"],
    "ecommerce":  ["about", "contact", "product", "faq", "blog"],
    "local":      ["about", "contact", "services", "faq"],
    "saas":       ["about", "contact", "product", "resources", "faq", "case_studies"],
    "agency":     ["about", "contact", "services", "case_studies", "resources", "industries"],
    "publisher":  ["about", "contact", "blog", "resources"],
    "enterprise": ["about", "contact", "services", "industries", "resources", "case_studies", "product"],
}

# Per-model deterministic signals: token -> weight (presence-based, weight counted
# once per token that fires). `blog_ratio_*` rewards a publisher-shaped page mix;
# `site_class_bonus` lets the enterprise model lean on the classified tier.
_DEFAULT_MODEL_SIGNALS: dict[str, dict[str, Any]] = {
    "ecommerce":  {"slug_tokens": {"cart": 3, "checkout": 3, "shop": 2, "store": 2, "product": 1, "products": 1, "basket": 3, "collections": 2, "add-to-cart": 3}},
    "saas":       {"slug_tokens": {"pricing": 3, "signup": 2, "sign-up": 2, "demo": 2, "trial": 2, "free-trial": 2, "integrations": 2, "docs": 1, "api": 1, "login": 1, "features": 1}},
    "local":      {"slug_tokens": {"location": 3, "locations": 3, "directions": 3, "book": 2, "appointment": 3, "hours": 2, "near-me": 3, "visit": 2}},
    "agency":     {"slug_tokens": {"case-stud": 3, "clients": 2, "portfolio": 3, "our-work": 3, "services": 1, "case-studies": 3}},
    "publisher":  {"slug_tokens": {"articles": 2, "news": 2, "category": 2, "topics": 2, "blog": 1}, "blog_ratio_weight": 4, "blog_ratio_threshold": 0.4},
    "lead_gen":   {"slug_tokens": {"quote": 3, "consultation": 3, "get-started": 2, "contact": 1, "solution": 1, "solutions": 1, "request-demo": 2}},
    "enterprise": {"slug_tokens": {"investors": 3, "careers": 1, "press": 2, "global": 2, "annual-report": 3, "leadership": 2, "newsroom": 2}, "site_class_bonus": {"large": 2, "enterprise": 4}},
}

# Onboarding industry/topic text -> model boost (each matched keyword adds a fixed nudge).
_DEFAULT_INDUSTRY_HINTS: dict[str, list[str]] = {
    "saas":      ["software", "saas", "platform", "b2b", "cloud", "api"],
    "ecommerce": ["ecommerce", "e-commerce", "retail", "shop", "store", "dtc", "marketplace"],
    "local":     ["restaurant", "clinic", "dentist", "salon", "law firm", "plumber", "local"],
    "agency":    ["agency", "consultancy", "studio"],
    "publisher": ["media", "magazine", "news", "publisher"],
}

# 5-stage funnel: a page maps to a stage by page_type OR slug token.
_DEFAULT_STAGE_SIGNALS: dict[str, dict[str, list[str]]] = {
    "awareness":     {"page_types": ["blog", "pillar"],     "slug_tokens": ["blog", "guide", "what-is", "learn", "resource", "resources", "article"]},
    "consideration": {"page_types": ["solution", "pillar"], "slug_tokens": ["solution", "solutions", "vs", "compare", "comparison", "alternative", "how-to", "use-case"]},
    "decision":      {"page_types": ["product"],            "slug_tokens": ["pricing", "product", "platform", "demo", "features", "request-demo"]},
    "conversion":    {"page_types": ["contact"],            "slug_tokens": ["contact", "signup", "sign-up", "checkout", "cart", "quote", "book", "get-started", "trial", "free-trial", "consultation"]},
    "retention":     {"page_types": [],                     "slug_tokens": ["support", "docs", "help", "faq", "account", "community", "changelog", "onboarding", "login", "status", "knowledge-base"]},
}

_DEFAULT_SCENARIO_MAP: dict[str, str] = {
    "none": "no_website", "single_page": "single_page", "small": "small_site",
    "medium": "growing_site", "large": "mature_site", "enterprise": "mature_site",
}

_DEFAULT_DELIVERABLES: dict[str, str] = {
    "no_website": "AEO Website Blueprint",
    "single_page": "Restructuring Roadmap",
    "small_site": "Gap Analysis & Build Plan",
    "growing_site": "Gap Analysis & Authority Plan",
    "mature_site": "Consolidation & Authority Plan",
}


@dataclass(slots=True)
class IntelligenceCfg:
    thresholds: dict[str, int]
    archetypes: dict[str, dict[str, list[str]]]
    expected_archetypes: dict[str, list[str]]
    model_signals: dict[str, dict[str, Any]]
    llm_tiebreak_margin: float
    industry_hints: dict[str, list[str]]
    stage_signals: dict[str, dict[str, list[str]]]
    scenario_map: dict[str, str]
    deliverables: dict[str, str]


def _merge(default: dict[str, Any], override: Any) -> dict[str, Any]:
    """Shallow section-level merge: an override dict replaces matching top-level keys,
    everything else keeps the default. A non-dict override is ignored (keep default)."""
    if not isinstance(override, dict):
        return dict(default)
    out = dict(default)
    out.update(override)
    return out


@lru_cache(maxsize=1)
def load_intelligence_cfg() -> IntelligenceCfg:
    raw = load_yaml_file("intelligence.yaml") or {}
    cls = raw.get("classification", {}) or {}
    bm = raw.get("business_model", {}) or {}
    jr = raw.get("journey", {}) or {}
    sc = raw.get("scenario", {}) or {}
    return IntelligenceCfg(
        thresholds=_merge(_DEFAULT_THRESHOLDS, cls.get("thresholds")),
        archetypes=_merge(_DEFAULT_ARCHETYPES, cls.get("archetypes")),
        expected_archetypes=_merge(_DEFAULT_EXPECTED, cls.get("expected_archetypes")),
        model_signals=_merge(_DEFAULT_MODEL_SIGNALS, bm.get("signals")),
        llm_tiebreak_margin=float(bm.get("llm_tiebreak_margin", 0.15)),
        industry_hints=_merge(_DEFAULT_INDUSTRY_HINTS, bm.get("industry_hints")),
        stage_signals=_merge(_DEFAULT_STAGE_SIGNALS, jr.get("stage_signals")),
        scenario_map=_merge(_DEFAULT_SCENARIO_MAP, sc.get("map")),
        deliverables=_merge(_DEFAULT_DELIVERABLES, sc.get("deliverables")),
    )
