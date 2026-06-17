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


def test_relaxed_prompt_drops_hard_constraint():
    p = _discovery_prompt("Acme", "acme.com", "Healthcare", "Austin, TX", 5, strict=False)
    # Industry/location still appear as soft context, but the hard "MUST" clause is gone.
    assert "Healthcare industry" in p
    assert "Every competitor MUST" not in p


def test_discover_competitors_no_llm_is_empty():
    # No LLM → empty result, never raises (onboarding proceeds by hand).
    assert discover_competitors("Acme", "acme.com", topic="Healthcare", location="Austin").verified == []


class _FakeLLM:
    """Minimal LLM stub. ``answer_when(predicate, competitors)`` rules fire by prompt text;
    every prompt is recorded so tests can assert which relaxation passes ran."""

    enabled = True

    def __init__(self, rules):
        self._rules = rules
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, system: str) -> dict:
        self.prompts.append(prompt)
        for predicate, competitors in self._rules:
            if predicate(prompt):
                return {"competitors": competitors}
        return {"competitors": []}


def _comp(name: str, domain: str) -> dict:
    return {"name": name, "domain": domain, "aliases": []}


def test_relaxes_location_when_strict_pass_is_empty():
    # Strict city pass returns nothing; the broadened (state) pass supplies peers.
    llm = _FakeLLM([
        (lambda p: "SAME market (TX)" in p, [_comp("Beta Health", "beta.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Beta Health"]
    assert result.relaxed is True
    # The very first pass was the strict city ask.
    assert "SAME market (Austin, TX)" in llm.prompts[0]


def test_softens_industry_as_last_resort():
    # Nothing matches until the hard-constraint clause is dropped entirely.
    llm = _FakeLLM([
        (lambda p: "Every competitor MUST" not in p, [_comp("Gamma", "gamma.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Gamma"]
    assert result.relaxed is True


def test_strict_pass_wins_without_relaxing():
    llm = _FakeLLM([
        (lambda p: "SAME market (Austin, TX)" in p, [_comp("Delta", "delta.com")]),
    ])
    result = discover_competitors(
        "Acme", "acme.com", topic="Healthcare", location="Austin, TX",
        llm=llm, head_check=lambda _d: True,
    )
    assert [c.name for c in result.verified] == ["Delta"]
    assert result.relaxed is False
    assert len(llm.prompts) == 1  # stopped at the strict pass, no wasted calls
