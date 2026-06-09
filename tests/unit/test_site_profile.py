"""SiteProfile aggregate — end-to-end build, JSONB round-trip, determinism."""

from __future__ import annotations

import json
from types import SimpleNamespace

from aeo.intelligence import SiteClass, build_site_profile
from aeo.intelligence.business_intent import BusinessModel
from aeo.intelligence.scenario import Scenario
from aeo.processor.coverage_diff import CoverageDiffResult, MissingNode


def _scored(url: str, page_type: str = "default") -> SimpleNamespace:
    return SimpleNamespace(url=url, page_type=page_type)


def _saas_site() -> list[SimpleNamespace]:
    return [
        _scored("https://acme.com/", "homepage"),
        _scored("https://acme.com/pricing", "product"),
        _scored("https://acme.com/demo", "product"),
        _scored("https://acme.com/integrations", "default"),
        _scored("https://acme.com/blog/post", "blog"),
        _scored("https://acme.com/about", "about"),
    ]


def test_build_end_to_end() -> None:
    prof = build_site_profile(domain="acme.com", discovered=_saas_site(), topic="B2B SaaS platform")
    assert prof.classification.site_class is SiteClass.SMALL
    assert prof.business_intent.model is BusinessModel.SAAS
    assert prof.strategy.scenario is Scenario.SMALL_SITE
    assert prof.strategy.deliverable == "Gap Analysis & Build Plan"
    assert prof.headline()


def test_to_dict_is_jsonb_serializable() -> None:
    coverage = CoverageDiffResult(
        topic="t", blueprint_version=1, total_nodes=2, matched=["/"],
        missing=[MissingNode(slug="/contact", title="Contact", page_type="contact",
                             intent="navigational", journey_stage="decision", cluster=None, priority=0.6)],
        thin_clusters=[],
    )
    prof = build_site_profile(domain="acme.com", discovered=_saas_site(), coverage=coverage)
    d = prof.to_dict()
    # round-trips through JSON unchanged (no enum objects leaked)
    again = json.loads(json.dumps(d))
    assert again["scenario"] == prof.strategy.scenario.value
    assert again["business_intent"]["model"] == prof.business_intent.model.value
    assert isinstance(again["actions"], list)
    assert {"scenario", "deliverable", "classification", "journey", "actions"} <= again.keys()


def test_deterministic_without_llm() -> None:
    a = build_site_profile(domain="acme.com", discovered=_saas_site(), topic="saas")
    b = build_site_profile(domain="acme.com", discovered=_saas_site(), topic="saas")
    assert a.to_dict() == b.to_dict()


def test_build_does_not_mutate_cached_cfg() -> None:
    # The lru_cached IntelligenceCfg must be read-only: a build (incl. industry hints
    # + a tie) must not write back into it.
    import copy

    from aeo.intelligence.config import load_intelligence_cfg

    cfg = load_intelligence_cfg()
    snapshot = copy.deepcopy(cfg)
    build_site_profile(domain="acme.com", discovered=_saas_site(), topic="saas software platform")
    assert cfg == snapshot


def test_empty_discovery_routes_no_website() -> None:
    prof = build_site_profile(domain="newco.com", discovered=[])
    assert prof.classification.site_class is SiteClass.NONE
    assert prof.strategy.scenario is Scenario.NO_WEBSITE
    assert prof.strategy.deliverable == "AEO Website Blueprint"
