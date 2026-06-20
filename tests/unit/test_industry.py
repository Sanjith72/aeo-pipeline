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
    parse_sparql_profile,
    resolve_wikidata_industry,
    resolve_wikidata_profile,
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


def test_keywords_match_only_at_word_boundary():
    # The "spa" ⊂ "workspace" class of false positive — substring matching firing mid-word.
    assert match_vertical("the all-in-one workspace for teams") is None
    assert match_vertical("submit your application") is None  # "it"/"app" not mid-word hits
    # …but a real day spa still resolves, and the intentional stems still prefix-match.
    assert match_vertical("a luxury day spa and salon") == "Beauty / Wellness"
    assert match_vertical("cybersecurity platform") == "Cybersecurity"  # stem prefix kept
    assert match_vertical("manufacturing company") == "Manufacturing"


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


def test_sparql_query_uses_anchored_host_regex_not_substring():
    from aeo.intelligence.industry import _host_regex, _sparql_query

    q = _sparql_query("ibm.com")
    # Anchored REGEX on the official-website value, not a free CONTAINS substring scan.
    assert "REGEX(STR(?website)" in q
    assert "CONTAINS" not in q
    # The pattern anchors start/end and allows an optional www, with the dot escaped for
    # the SPARQL string literal (\\. → regex \.), so it matches only the domain's own root.
    pat = _host_regex("ibm.com")
    assert pat == r"^https?://(www\\.)?ibm\\.com/?$"
    # A coincidental-substring host (e.g. "notibm.com") yields a DIFFERENT anchored pattern,
    # so the engine can't conflate them the way CONTAINS did.
    assert _host_regex("notibm.com") != pat


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
    # The registrable host is matched via an ANCHORED regex on P856 — never a substring
    # CONTAINS (which pulled wrong/subsidiary entities). The host root must still be present.
    assert "REGEX" in captured["query"]
    assert "CONTAINS" not in captured["query"]
    assert "clevelandclinic" in captured["query"]


def test_resolve_wikidata_industry_no_match_returns_none():
    async def empty_fetch(query: str):
        return {"results": {"bindings": []}}

    assert asyncio.run(resolve_wikidata_industry("unknown-biz.example", fetch=empty_fetch)) is None


def test_wikidata_lookups_are_cached_per_registrable_domain(monkeypatch):
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    calls = {"n": 0}

    async def counting_fetch(query: str):
        calls["n"] += 1
        return _cleveland_clinic_sparql()

    # Cache is active only on the default-fetch path (no injected fetch).
    monkeypatch.setattr(industry, "_default_sparql_fetch", counting_fetch)
    a = asyncio.run(industry.resolve_wikidata_industry("clevelandclinic.org"))
    b = asyncio.run(industry.resolve_wikidata_industry("https://www.clevelandclinic.org/"))
    assert a == b == "Healthcare"
    assert calls["n"] == 1  # second call (same registrable domain) served from cache
    industry.clear_wikidata_cache()


# ── richer "About you" profile (HQ / products / description) ─────────────────────


def _acme_profile_sparql() -> dict:
    # Shape mirrors WDQS JSON for a company with HQ (P159) + its country (P17), two
    # products produced (P1056), and an English schema:description. Industry P452 is a
    # generic class but P31 carries the specific vertical (software company).
    return {
        "results": {
            "bindings": [
                {
                    "industryLabel": {"value": "business enterprise"},
                    "instanceLabel": {"value": "software company"},
                    "hqLabel": {"value": "London"},
                    "countryLabel": {"value": "United Kingdom"},
                    "productLabel": {"value": "Endpoint Protection"},
                    "description": {"value": "British cybersecurity software company"},
                },
                {"productLabel": {"value": "Threat Intelligence"}},
                {"productLabel": {"value": "Endpoint Protection"}},  # dupe → deduped
            ]
        }
    }


def test_parse_sparql_profile_pulls_hq_products_description():
    p = parse_sparql_profile(_acme_profile_sparql())
    assert p.industry == "Software / SaaS"  # falls through generic P452 to specific P31
    assert p.location == "London"
    assert p.country == "United Kingdom"
    assert p.offerings == ["Endpoint Protection", "Threat Intelligence"]  # deduped, in order
    assert p.description == "British cybersecurity software company"
    assert p.has_signal() is True


def test_parse_sparql_profile_generic_only_industry_falls_through():
    # Generic-only P452/P31 → industry None, but HQ/description still parse independently.
    data = {
        "results": {
            "bindings": [
                {
                    "instanceLabel": {"value": "business enterprise"},
                    "hqLabel": {"value": "Berlin"},
                    "description": {"value": "A privately held company"},
                }
            ]
        }
    }
    p = parse_sparql_profile(data)
    assert p.industry is None
    assert p.location == "Berlin"
    assert p.description == "A privately held company"
    assert p.offerings == []


def test_parse_sparql_profile_empty_is_blank_no_signal():
    for data in (None, {}, {"results": {"bindings": []}}):
        p = parse_sparql_profile(data)
        assert p.industry is None and p.location is None and p.offerings == []
        assert p.has_signal() is False


def test_resolve_wikidata_profile_with_injected_fetch():
    captured = {}

    async def fake_fetch(query: str):
        captured["query"] = query
        return _acme_profile_sparql()

    out = asyncio.run(resolve_wikidata_profile("https://www.acme.io/", fetch=fake_fetch))
    assert out.industry == "Software / SaaS"
    assert out.location == "London"
    assert out.offerings == ["Endpoint Protection", "Threat Intelligence"]
    # the richer query asks for HQ / products / description on top of industry/instance
    assert "P159" in captured["query"] and "P1056" in captured["query"]
    assert "schema:description" in captured["query"]


def test_resolve_wikidata_profile_no_entity_is_blank():
    async def empty_fetch(query: str):
        return {"results": {"bindings": []}}

    out = asyncio.run(resolve_wikidata_profile("unknown-biz.example", fetch=empty_fetch))
    assert out.has_signal() is False


def test_resolve_wikidata_profile_network_failure_is_blank():
    async def dead_fetch(query: str):
        return None

    out = asyncio.run(resolve_wikidata_profile("acme.io", fetch=dead_fetch, use_cache=False))
    assert out.has_signal() is False


def test_resolve_wikidata_profile_cached_per_registrable_domain(monkeypatch):
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    calls = {"n": 0}

    async def counting_fetch(query: str):
        calls["n"] += 1
        return _acme_profile_sparql()

    monkeypatch.setattr(industry, "_default_sparql_fetch", counting_fetch)
    a = asyncio.run(industry.resolve_wikidata_profile("acme.io"))
    b = asyncio.run(industry.resolve_wikidata_profile("https://www.acme.io/"))
    assert a.location == b.location == "London"
    assert calls["n"] == 1  # second call (same registrable domain) served from cache
    industry.clear_wikidata_cache()


def test_wikidata_transient_failure_is_not_cached(monkeypatch):
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    calls = {"n": 0}

    async def flaky_fetch(query: str):
        calls["n"] += 1
        return None if calls["n"] == 1 else _cleveland_clinic_sparql()

    monkeypatch.setattr(industry, "_default_sparql_fetch", flaky_fetch)
    assert asyncio.run(industry.resolve_wikidata_industry("clevelandclinic.org")) is None  # network hiccup
    # A None response is never cached, so the next call retries and now succeeds.
    assert asyncio.run(industry.resolve_wikidata_industry("clevelandclinic.org")) == "Healthcare"
    assert calls["n"] == 2
    industry.clear_wikidata_cache()
