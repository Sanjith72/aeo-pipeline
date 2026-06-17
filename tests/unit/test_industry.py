"""
Industry resolution — specific verticals from Wikidata (P452/P31) + a crawl-text
classifier, both funnelling through one keyword map. Pure + offline (injectable SPARQL fetch).
"""

from __future__ import annotations

import asyncio

from aeo.intelligence.industry import (
    classify_vertical,
    map_wikidata_labels,
    match_vertical,
    parse_sparql_industry,
    resolve_wikidata_industry,
)


def test_generic_labels_are_ignored():
    for generic in ["Enterprise", "Business enterprise", "company", "Corporation", "organization"]:
        assert match_vertical(generic) is None


def test_specific_verticals_map():
    assert match_vertical("hospital") == "Healthcare"
    assert match_vertical("health care") == "Healthcare"
    assert match_vertical("retail bank") == "Finance"
    assert match_vertical("insurance company") == "Insurance"
    assert match_vertical("real estate agency") == "Real Estate"
    assert match_vertical("fast food restaurant") == "Restaurants"
    assert match_vertical("software company") == "Software / SaaS"


def test_map_wikidata_prefers_industry_then_instance_and_skips_generic():
    # P452 generic, P31 specific → falls through the generic to the hospital instance.
    assert map_wikidata_labels(["business enterprise"], ["hospital"]) == "Healthcare"
    # An industry label wins over a later instance label.
    assert map_wikidata_labels(["banking"], ["public company"]) == "Finance"
    # Nothing specific anywhere → None (caller falls back).
    assert map_wikidata_labels(["business enterprise"], ["company"]) is None


def test_classify_vertical_from_text_and_services():
    assert classify_vertical("We are a full-service dental clinic.") == "Healthcare"
    assert classify_vertical("", services=["Mortgage lending", "wealth management"]) == "Finance"
    assert classify_vertical("generic corporate site", topic="cybersecurity") == "Cybersecurity"
    assert classify_vertical("just some words about us") is None


def _cleveland_clinic_sparql() -> dict:
    # Shape mirrors WDQS JSON for Cleveland Clinic (Q13780930): industry=health care,
    # instance-of includes a generic class plus a hospital network.
    return {
        "results": {
            "bindings": [
                {"industryLabel": {"value": "health care"}, "instanceLabel": {"value": "nonprofit organization"}},
                {"instanceLabel": {"value": "hospital network"}},
            ]
        }
    }


def test_parse_sparql_industry_resolves_healthcare():
    assert parse_sparql_industry(_cleveland_clinic_sparql()) == "Healthcare"


def test_parse_sparql_industry_generic_only_is_none():
    data = {"results": {"bindings": [{"instanceLabel": {"value": "business enterprise"}}]}}
    assert parse_sparql_industry(data) is None
    assert parse_sparql_industry(None) is None
    assert parse_sparql_industry({}) is None


def test_resolve_wikidata_industry_with_injected_fetch():
    captured = {}

    async def fake_fetch(query: str):
        captured["query"] = query
        return _cleveland_clinic_sparql()

    out = asyncio.run(resolve_wikidata_industry("https://www.clevelandclinic.org/", fetch=fake_fetch))
    assert out == "Healthcare"
    # the registrable domain (no scheme/www) is what we match Wikidata's P856 on
    assert "clevelandclinic.org" in captured["query"]


def test_resolve_wikidata_industry_no_match_returns_none():
    async def empty_fetch(query: str):
        return {"results": {"bindings": []}}

    assert asyncio.run(resolve_wikidata_industry("unknown-biz.example", fetch=empty_fetch)) is None
