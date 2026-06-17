"""Competitor discovery prompt — strictly centers the specific industry + location."""

from __future__ import annotations

from aeo.reference.competitor_discovery import _discovery_prompt, discover_competitors


def test_prompt_requires_same_industry_and_location():
    p = _discovery_prompt("Acme", "acme.com", "Healthcare", "Austin, TX", 5)
    assert "Healthcare industry" in p
    assert "Austin, TX" in p
    assert "SAME industry (Healthcare)" in p
    assert "SAME market (Austin, TX)" in p


def test_prompt_includes_services_when_present():
    p = _discovery_prompt("Acme", "acme.com", "Finance", None, 5, services=["Mortgage lending"])
    assert "Mortgage lending" in p
    assert "SAME industry (Finance)" in p
    assert "market" not in p.split("MUST", 1)[-1]  # no location constraint when location is None


def test_discover_competitors_no_llm_is_empty():
    # No LLM → empty result, never raises (onboarding proceeds by hand).
    assert discover_competitors("Acme", "acme.com", topic="Healthcare", location="Austin").verified == []
