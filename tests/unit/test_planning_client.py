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
