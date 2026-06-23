"""Research agent: discover + live-verify competitors (deterministic-first)."""

from __future__ import annotations

from aeo.agents.research import research_competitors


class FakeLLM:
    enabled = True

    def generate_json(self, prompt, system=None):
        return {"competitors": [
            {"name": "Rapid7", "domain": "rapid7.com"},
            {"name": "Tenable", "domain": "tenable.com"},
        ]}


def test_returns_verified_competitors() -> None:
    out = research_competitors(
        {"name": "Acme", "domain": "acme.com", "topic": "ctem"},
        llm=FakeLLM(), head_check=lambda domain: True,  # all reachable
    )
    assert [c["domain"] for c in out["competitors"]] == ["rapid7.com", "tenable.com"]
    assert out["dropped"] == []


def test_drops_unreachable_domains() -> None:
    out = research_competitors(
        {"name": "Acme", "domain": "acme.com", "topic": "ctem"},
        llm=FakeLLM(), head_check=lambda domain: domain == "rapid7.com",
    )
    assert [c["domain"] for c in out["competitors"]] == ["rapid7.com"]
    assert [c["domain"] for c in out["dropped"]] == ["tenable.com"]


def test_empty_without_llm() -> None:
    out = research_competitors({"name": "Acme"}, llm=None)
    assert out["competitors"] == []
