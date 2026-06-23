"""Hybrid planning tier: LLMCfg.planning_provider + AgentsCfg build/research flags + get_planning_client()."""

from __future__ import annotations


def test_llmcfg_has_planning_provider_default_empty() -> None:
    from aeo.settings import LLMCfg

    assert LLMCfg().planning_provider == ""


def test_agentscfg_has_build_and_research_flags() -> None:
    from aeo.settings import AgentsCfg

    cfg = AgentsCfg()
    assert cfg.research_enabled is True
    assert cfg.build_enabled is True
    assert cfg.draft_limit == 5


def test_get_planning_client_falls_back_to_primary_when_unset() -> None:
    from aeo.nlp import llm as llm_mod

    llm_mod.get_client.cache_clear()
    llm_mod.get_planning_client.cache_clear()
    # default test env: planning_provider unset → same client object as primary
    assert llm_mod.get_planning_client() is llm_mod.get_client()


def test_get_planning_client_routes_to_planning_provider(monkeypatch) -> None:
    from aeo.nlp import llm as llm_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().llm, "provider", "ollama")
    monkeypatch.setattr(get_settings().llm, "planning_provider", "cloud")
    llm_mod.get_planning_client.cache_clear()
    client = llm_mod.get_planning_client()
    assert client.provider == "cloud"
