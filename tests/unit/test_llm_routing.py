"""Hybrid LLM routing: the async deep-audit burst runs on a local model (un-throttled) while
the fast synchronous endpoints stay on the primary (cloud) provider."""

from __future__ import annotations

from aeo.nlp import llm as llm_mod
from aeo.nlp.llm import LLMClient
from aeo.settings import LLMCfg


def test_bulk_client_forces_the_local_backend_when_provider_differs() -> None:
    # primary=cloud + bulk=ollama → a bulk client uses the LOCAL ollama backend (local model),
    # so the audit dodges cloud rate limits, while the primary client stays cloud.
    cfg = LLMCfg(
        provider="cloud", bulk_provider="ollama",
        model="qwen2.5:3b", cloud_model="gemini-2.5-flash", cloud_api_key="x",
    )
    primary = LLMClient(cfg)
    bulk = LLMClient(cfg.model_copy(update={"provider": cfg.bulk_provider}))
    assert (primary.provider, primary.model) == ("cloud", "gemini-2.5-flash")
    assert (bulk.provider, bulk.model) == ("ollama", "qwen2.5:3b")


def test_get_bulk_client_falls_back_to_primary_when_unset(monkeypatch) -> None:
    # no bulk_provider → bulk IS the primary (no behavior change for non-hybrid deployments).
    monkeypatch.setattr(llm_mod.get_settings().llm, "bulk_provider", "")
    llm_mod.get_client.cache_clear()
    llm_mod.get_bulk_client.cache_clear()
    try:
        assert llm_mod.get_bulk_client() is llm_mod.get_client()
    finally:
        llm_mod.get_client.cache_clear()
        llm_mod.get_bulk_client.cache_clear()
