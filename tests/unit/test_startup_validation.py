"""
Startup validation (aeo/startup.py): fatal on config that can never work, warning on
degraded-but-functional — the boundary the deterministic-first contract depends on.
"""

from __future__ import annotations

import pytest

from aeo import startup as startup_mod
from aeo.settings import DatabaseCfg, Settings
from aeo.startup import StartupValidationError, validate_settings


@pytest.fixture()
def settings(monkeypatch) -> Settings:
    """A mutable Settings the validator sees instead of the process-wide cached one."""
    s = Settings()
    monkeypatch.setattr(startup_mod, "get_settings", lambda: s)
    return s


class TestFatal:
    def test_bad_database_scheme(self, settings):
        settings.database = DatabaseCfg(url="mysql://root@localhost/aeo")
        with pytest.raises(StartupValidationError, match="postgresql://"):
            validate_settings()

    def test_pool_bounds(self, settings):
        settings.database = DatabaseCfg(url="postgresql://a:b@localhost:5432/aeo",
                                        pool_min=5, pool_max=2)
        with pytest.raises(StartupValidationError, match="DB_POOL_MAX"):
            validate_settings()

    def test_unknown_llm_provider(self, settings):
        settings.llm.provider = "gpt-neo-self-hosted"
        with pytest.raises(StartupValidationError, match="AEO__LLM__PROVIDER"):
            validate_settings()

    def test_blank_auth_key_is_rejected(self, settings):
        settings.api.auth_key = "   "
        with pytest.raises(StartupValidationError, match="AUTH_KEY"):
            validate_settings()

    def test_all_problems_reported_at_once(self, settings):
        settings.database = DatabaseCfg(url="mysql://x@h/db")
        settings.llm.provider = "nope"
        with pytest.raises(StartupValidationError) as excinfo:
            validate_settings()
        assert len(excinfo.value.problems) == 2


class TestWarnings:
    def test_hybrid_without_keys_warns_but_boots(self, settings):
        settings.llm.enabled = True
        settings.llm.provider = "hybrid"
        settings.llm.gemini_api_key = None
        settings.llm.qwen_api_key = None
        warnings = validate_settings()
        assert any("GEMINI_API_KEY" in w for w in warnings)
        assert any("QWEN_API_KEY" in w for w in warnings)

    def test_serving_unauthenticated_warns(self, settings):
        settings.api.auth_key = None
        warnings = validate_settings(serving=True)
        assert any("unauthenticated" in w for w in warnings)

    def test_not_serving_does_not_warn_about_auth(self, settings):
        settings.api.auth_key = None
        assert not any("unauthenticated" in w for w in validate_settings())

    def test_supabase_direct_host_warns_about_ipv6_and_ssl(self, settings, monkeypatch):
        monkeypatch.delenv("PGSSLMODE", raising=False)
        settings.database = DatabaseCfg(url="postgresql://p:w@db.abc123.supabase.co:5432/postgres")
        warnings = validate_settings()
        assert any("IPv6-only" in w for w in warnings)
        assert any("sslmode" in w for w in warnings)

    def test_pooler_url_with_sslmode_is_clean(self, settings):
        settings.database = DatabaseCfg(
            url="postgresql://p.w:x@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        )
        settings.llm.enabled = False
        assert validate_settings() == []
