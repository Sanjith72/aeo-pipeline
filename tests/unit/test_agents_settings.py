"""AgentsCfg: defaults + AEO__AGENTS__* env override."""

from __future__ import annotations


def test_agents_cfg_defaults() -> None:
    from aeo.settings import AgentsCfg

    cfg = AgentsCfg()
    assert cfg.concurrency == 2
    assert cfg.step_timeout_sec == 120
    assert cfg.max_attempts == 3


def test_settings_exposes_agents_section() -> None:
    from aeo.settings import AgentsCfg, Settings

    s = Settings()
    assert isinstance(s.agents, AgentsCfg)


def test_agents_env_override(monkeypatch) -> None:
    from aeo.settings import Settings

    monkeypatch.setenv("AEO__AGENTS__CONCURRENCY", "5")
    s = Settings()
    assert s.agents.concurrency == 5
