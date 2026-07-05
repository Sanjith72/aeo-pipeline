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


def test_builder_keyword_is_construction_specific():
    # "builder" alone is too broad: a "website builder" SaaS must NOT read as Construction,
    # while genuine construction trades (home/pool/deck builder) still do.
    assert classify_vertical("Squarespace — the all-in-one website builder") != "Construction"
    assert classify_vertical("The fastest app builder for teams") != "Construction"
    assert classify_vertical("Acme Home Builders — custom homes in Austin") == "Construction"
    assert classify_vertical("Premier Pool Builder & spa installation") == "Construction"


def test_free_shipping_is_not_logistics():
    # "free shipping" is e-commerce boilerplate, not a logistics company.
    assert classify_vertical("Comfortable shoes & apparel. FREE shipping & returns.") != "Logistics / Transportation"
    # A genuine freight carrier still classifies.
    assert classify_vertical("Global freight shipping and container logistics") == "Logistics / Transportation"


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


def test_match_vertical_sweep_regressions():
    # Each of these mislabeled or blanked a real site in the 2026-07-05 live sweep.
    assert match_vertical("Zillow: Real Estate, Apartments, Mortgages & Home Values") == "Real Estate"
    assert match_vertical("Managed IT Services and Global Workforce Solutions") == "Software / SaaS"
    assert match_vertical("Explore SUVs, hybrids and minivans") == "Automotive"
    assert match_vertical("Help those affected by disasters by making a donation") == "Nonprofit"
    assert match_vertical("humanitarian aid across 190 countries") == "Nonprofit"
    # A pure lender still reads Finance — the Real-Estate-first ordering only wins when
    # an explicit real-estate token is present.
    assert match_vertical("Low-rate mortgage lending for first-time buyers") == "Finance"
    # A title insurer says real-estate words but IS insurance — Insurance stays above.
    assert match_vertical("Title insurance for real estate closings") == "Insurance"
    # "managed it!" is exact-ended: the phrase matches, prefix collisions don't.
    assert match_vertical("Managed IT support for growing teams") == "Software / SaaS"
    assert match_vertical("Expertly managed itineraries for corporate travel") is None


# ── entity-API fast path (the default retrieval for resolve_wikidata_profile) ────


def _stmt(qid: str) -> dict:
    """One claim in wbgetentities shape whose value is an entity reference."""
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def _mayo_like_responses() -> tuple[dict, dict, dict]:
    """(search, entities, labels) responses mirroring the live shapes for a hospital."""
    search = {"query": {"search": [{"title": "Q1130172"}]}}
    entities = {
        "entities": {
            "Q1130172": {
                "claims": {
                    "P452": [_stmt("Q31207")],
                    "P31": [_stmt("Q163740")],
                    "P159": [_stmt("Q486479")],
                },
                "descriptions": {
                    "en": {"value": "medical practice and medical research group"}
                },
                "sitelinks": {"enwiki": {}, "dewiki": {}},
            }
        }
    }
    labels = {
        "entities": {
            "Q31207": {"labels": {"en": {"value": "health care"}}},
            "Q163740": {"labels": {"en": {"value": "nonprofit organization"}}},
            "Q486479": {"labels": {"en": {"value": "Rochester"}}},
        }
    }
    return search, entities, labels


def _dispatch_api_fetch(search: dict | None, entities: dict | None, labels: dict | None,
                        seen: list | None = None):
    """A fake ApiFetch that answers each of the three call shapes."""

    async def fetch(params: dict):
        if seen is not None:
            seen.append(params)
        if params.get("list") == "search":
            return search
        if "claims" in str(params.get("props", "")):
            return entities
        return labels

    return fetch


def test_statement_search_query_is_exact_p856_variants():
    from aeo.intelligence.industry import _statement_search_query

    q = _statement_search_query("ibm.com")
    # Exact statement matching, never a substring scan — all realistic P856 forms OR'd.
    assert q.startswith("haswbstatement:")
    for variant in ("https://www.ibm.com", "https://ibm.com/", "http://ibm.com"):
        assert f"P856={variant}" in q
    assert q.count("P856=") == 8


def test_resolve_profile_entity_api_resolves_healthcare():
    seen: list = []
    fetch = _dispatch_api_fetch(*_mayo_like_responses(), seen=seen)
    out = asyncio.run(resolve_wikidata_profile("https://www.mayoclinic.org/", api_fetch=fetch))
    assert out.industry == "Healthcare"  # P452 "health care" label
    assert out.location == "Rochester"
    assert out.description == "medical practice and medical research group"
    # exact-statement search on the registrable domain, then entities, then labels
    assert "P856=https://mayoclinic.org" in seen[0]["srsearch"]
    assert len(seen) == 3


def test_resolve_profile_entity_api_description_is_industry_tiebreak():
    # Generic-only P452/P31 labels → the entity's own description names the vertical.
    search, entities, _ = _mayo_like_responses()
    entities["entities"]["Q1130172"]["descriptions"]["en"]["value"] = (
        "British cybersecurity software company"
    )
    labels = {
        "entities": {
            "Q31207": {"labels": {"en": {"value": "business enterprise"}}},
            "Q163740": {"labels": {"en": {"value": "privately held company"}}},
            "Q486479": {"labels": {"en": {"value": "London"}}},
        }
    }
    out = asyncio.run(resolve_wikidata_profile("acme.io", api_fetch=_dispatch_api_fetch(search, entities, labels)))
    assert out.industry == "Cybersecurity"
    assert out.location == "London"


def test_resolve_profile_entity_api_picks_most_notable_entity():
    # A parent and its subsidiary share one official website — most sitelinks wins.
    search = {"query": {"search": [{"title": "Q1"}, {"title": "Q2"}]}}
    entities = {
        "entities": {
            "Q1": {
                "claims": {"P31": [_stmt("Q10")]},
                "descriptions": {"en": {"value": "subsidiary"}},
                "sitelinks": {"enwiki": {}},
            },
            "Q2": {
                "claims": {"P31": [_stmt("Q11")]},
                "descriptions": {"en": {"value": "parent"}},
                "sitelinks": {"enwiki": {}, "dewiki": {}, "frwiki": {}},
            },
        }
    }
    labels = {
        "entities": {
            "Q10": {"labels": {"en": {"value": "subsidiary"}}},
            "Q11": {"labels": {"en": {"value": "software company"}}},
        }
    }
    out = asyncio.run(resolve_wikidata_profile("acme.io", api_fetch=_dispatch_api_fetch(search, entities, labels)))
    assert out.industry == "Software / SaaS"
    assert out.description == "parent"


def test_resolve_profile_entity_api_drops_brand_echo_offering():
    # Nike's P1056 "products produced" is the item "Nike" itself — a brand echo is not
    # an answer to "what do you offer", so it must be dropped (falls to the description).
    search = {"query": {"search": [{"title": "Q483915"}]}}
    entities = {
        "entities": {
            "Q483915": {
                "labels": {"en": {"value": "Nike"}},
                "claims": {"P31": [_stmt("Q20")], "P1056": [_stmt("Q21"), _stmt("Q22")]},
                "descriptions": {"en": {"value": "American athletic footwear company"}},
                "sitelinks": {"enwiki": {}},
            }
        }
    }
    labels = {
        "entities": {
            "Q20": {"labels": {"en": {"value": "public company"}}},
            "Q21": {"labels": {"en": {"value": "Nike"}}},        # brand echo — dropped
            "Q22": {"labels": {"en": {"value": "sportswear"}}},  # a real offering — kept
        }
    }
    out = asyncio.run(resolve_wikidata_profile("nike.com", api_fetch=_dispatch_api_fetch(search, entities, labels)))
    assert out.offerings == ["sportswear"]


def test_resolve_profile_entity_api_no_entity_is_blank():
    fetch = _dispatch_api_fetch({"query": {"search": []}}, None, None)
    out = asyncio.run(resolve_wikidata_profile("unknown-biz.example", api_fetch=fetch))
    assert out.has_signal() is False


def test_resolve_profile_entity_api_failed_labels_degrades_to_description():
    # Labels batch fails transiently → industry still resolves from the description.
    search, entities, _ = _mayo_like_responses()
    fetch = _dispatch_api_fetch(search, entities, None)
    out = asyncio.run(resolve_wikidata_profile("mayoclinic.org", api_fetch=fetch, use_cache=False))
    assert out.industry == "Healthcare"  # "medical …" description
    assert out.location is None  # HQ label unavailable without the batch


def test_resolve_wikidata_profile_cached_per_registrable_domain(monkeypatch):
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    searches = {"n": 0}
    search, entities, labels = _mayo_like_responses()

    async def counting_fetch(params: dict):
        if params.get("list") == "search":
            searches["n"] += 1
            return search
        if "claims" in str(params.get("props", "")):
            return entities
        return labels

    # Cache is active only on the default-fetch path (no injected fetch).
    monkeypatch.setattr(industry, "_default_api_fetch", counting_fetch)
    a = asyncio.run(industry.resolve_wikidata_profile("mayoclinic.org"))
    b = asyncio.run(industry.resolve_wikidata_profile("https://www.mayoclinic.org/"))
    assert a.location == b.location == "Rochester"
    assert searches["n"] == 1  # second call (same registrable domain) served from cache
    industry.clear_wikidata_cache()


def test_resolve_profile_labels_degraded_result_not_cached(monkeypatch):
    # A transient failure of the LABELS batch (call 3) yields a usable but degraded
    # profile (description-only industry, no location/offerings). It must be served
    # once and NOT pinned in the cache — the retry after Wikimedia recovers must run
    # the lookup again and get the full profile.
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    searches = {"n": 0}
    labels_ok = {"v": False}
    search, entities, labels = _mayo_like_responses()

    async def fetch(params: dict):
        if params.get("list") == "search":
            searches["n"] += 1
            return search
        if "claims" in str(params.get("props", "")):
            return entities
        return labels if labels_ok["v"] else None

    monkeypatch.setattr(industry, "_default_api_fetch", fetch)
    degraded = asyncio.run(industry.resolve_wikidata_profile("mayoclinic.org"))
    assert degraded.industry == "Healthcare"  # description tiebreak still lands
    assert degraded.location is None  # no labels batch → no HQ label
    labels_ok["v"] = True
    full = asyncio.run(industry.resolve_wikidata_profile("mayoclinic.org"))
    assert full.location == "Rochester"  # retried, not served the degraded cache entry
    assert searches["n"] == 2
    industry.clear_wikidata_cache()


def test_api_fetch_error_payload_is_transient():
    # MediaWiki reports failures as HTTP-200 {"error": ...} payloads; those must behave
    # like a network failure (blank, retryable), never like a genuine no-entity miss.
    async def error_fetch(params: dict):
        return {"error": {"code": "ratelimited"}, "servedby": "mw1"} if params.get("list") == "search" else {}

    out = asyncio.run(resolve_wikidata_profile("acme.io", api_fetch=error_fetch, use_cache=False))
    assert out.has_signal() is False


def test_resolve_profile_transient_search_failure_not_cached(monkeypatch):
    from aeo.intelligence import industry

    industry.clear_wikidata_cache()
    searches = {"n": 0}
    search, entities, labels = _mayo_like_responses()

    async def flaky_fetch(params: dict):
        if params.get("list") == "search":
            searches["n"] += 1
            return None if searches["n"] == 1 else search
        if "claims" in str(params.get("props", "")):
            return entities
        return labels

    monkeypatch.setattr(industry, "_default_api_fetch", flaky_fetch)
    assert asyncio.run(industry.resolve_wikidata_profile("mayoclinic.org")).has_signal() is False
    # The failure was not cached as a miss — the retry succeeds.
    assert asyncio.run(industry.resolve_wikidata_profile("mayoclinic.org")).industry == "Healthcare"
    assert searches["n"] == 2
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
