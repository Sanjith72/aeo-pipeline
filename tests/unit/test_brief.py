"""SP-2 no-website entry path — brief → blueprint + no_website plan, no crawl."""

from __future__ import annotations

import json

from aeo.intelligence import BusinessModel, Scenario, SiteClass
from aeo.intelligence.brief import plan_from_brief
from aeo.reference.business_input import BusinessInput
from aeo.reference.framework import build_framework, load_framework


def test_business_input_key_from_domain_or_name() -> None:
    assert BusinessInput(name="Acme", domain="https://acme.com/").key() == "acme.com"
    assert BusinessInput(name="Acme Security Co").key() == "acme-security-co"  # slug from name
    assert BusinessInput(name="   ").key() == "site"  # never empty


def test_plan_from_brief_is_no_website_greenfield() -> None:
    framework = load_framework()  # the shared default framework is enough for the path
    brief = BusinessInput(name="Acme", domain="acme.com", topic="ctem")
    plan = plan_from_brief(brief, framework=framework)

    # No pages exist yet → NONE tier, no_website scenario, blueprint deliverable.
    assert plan.profile.classification.site_class is SiteClass.NONE
    assert plan.profile.strategy.scenario is Scenario.NO_WEBSITE
    assert plan.profile.strategy.deliverable == "AEO Website Blueprint"
    # The blueprint is non-empty and the coverage diff marks everything missing.
    assert len(plan.blueprint.sitemap) > 0
    assert plan.coverage.coverage_pct == 0.0
    assert len(plan.coverage.missing) == len(plan.blueprint.sitemap)
    # Greenfield → all 5 journey stages are gaps, and there is a build plan.
    assert len(plan.profile.journey.gaps) == 5
    assert plan.profile.strategy.actions
    # First actions are structure (build the site skeleton first).
    assert plan.profile.strategy.actions[0].category == "structure"


def test_plan_to_dict_is_jsonb_serializable() -> None:
    framework = load_framework()
    plan = plan_from_brief(BusinessInput(name="Acme", domain="acme.com"), framework=framework)
    again = json.loads(json.dumps(plan.to_dict()))
    assert again["profile"]["scenario"] == "no_website"
    assert again["blueprint"]["ideal_pages"] == len(plan.blueprint.sitemap)
    assert {"business", "blueprint", "coverage", "profile"} <= again.keys()
    assert again["business"]["key"] == "acme.com"


def test_in_memory_framework_from_brief_needs_no_file() -> None:
    # build_framework parses a raw bootstrap-style dict with no file I/O.
    raw = {
        "version": "1", "topic": "Widgets", "required_entities": ["Widget"],
        "journey_stages": ["awareness", "consideration", "decision"],
        "clusters": [{
            "name": "core", "min_pages": 3,
            "pillar": {"slug": "/widgets", "title": "Widgets", "page_type": "pillar",
                       "intent": "informational", "journey_stage": "awareness"},
            "supporting": [],
        }],
        "standalone_nodes": [],
    }
    framework = build_framework(raw)
    assert framework.topic == "Widgets"
    plan = plan_from_brief(BusinessInput(name="Widget Co", domain="widget.co"), framework=framework)
    assert plan.profile.strategy.scenario is Scenario.NO_WEBSITE
    assert any(n.slug == "/widgets" for n in plan.blueprint.sitemap)


def test_category_brief_influences_business_model() -> None:
    framework = load_framework()
    brief = BusinessInput(name="Shopify Store", domain="store.example", category="ecommerce retail")
    plan = plan_from_brief(brief, framework=framework)
    # industry hint 'ecommerce/retail' nudges the model even with no discovered pages
    assert plan.profile.business_intent.model is BusinessModel.ECOMMERCE
