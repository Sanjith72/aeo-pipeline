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

    def test_pool_min_zero_is_a_valid_lazy_pool(self, settings):
        # Serverless Postgres (Neon / Supabase free) deployments run DB_POOL_MIN=0 on
        # purpose — no idle connections held. Must boot cleanly.
        settings.database = DatabaseCfg(url="postgresql://a:b@h:5432/aeo?sslmode=require",
                                        pool_min=0, pool_max=5)
        settings.llm.enabled = False
        assert validate_settings() == []

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

    def test_serving_unauthenticated_is_fatal(self, settings):
        # Was a WARNING, which is the wrong severity: with neither AEO__API__AUTH_KEY nor
        # AEO__API__ADMIN_KEY set, require_admin_key used to fall through and leave
        # POST /api/entitlements/grant ungated — a public deploy that merely FORGOT a
        # variable handed out free all_packs grants. Refuse to boot instead.
        settings.api.auth_key = None
        settings.api.allow_open = False
        with pytest.raises(StartupValidationError, match="AEO__API__AUTH_KEY"):
            validate_settings(serving=True)

    def test_allow_open_is_the_named_escape_hatch(self, settings):
        # Local dev (scripts/run.ps1, docker compose) binds to localhost and legitimately
        # runs with no key — but has to SAY so. Downgraded to a loud warning, never silent.
        settings.api.auth_key = None
        settings.api.allow_open = True
        warnings = validate_settings(serving=True)
        assert any("ALLOW_OPEN" in w for w in warnings)

    def test_not_serving_does_not_care_about_auth(self, settings):
        # Every `aeo` CLI command bootstraps with serving=False; a one-shot crawl exposes
        # no HTTP surface, so it must never need an API key to run.
        settings.api.auth_key = None
        settings.api.allow_open = False
        assert not any("AUTH_KEY" in w for w in validate_settings())

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


# The JWKS URL is exercised only by a REAL login, so a wrong one boots perfectly clean and
# then 401s every user with an opaque "invalid token". These checks are the boot-time half
# of that gap (the network half is scripts/check_auth_config.py --live).
GOOD_JWKS = "https://klnzsbguvitpnixnvsqs.supabase.co/auth/v1/.well-known/jwks.json"


class TestJwksConfig:
    def test_placeholder_jwks_url_is_fatal(self, settings):
        settings.auth.jwks_url = "https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json"
        with pytest.raises(StartupValidationError, match="placeholder"):
            validate_settings()

    @pytest.mark.parametrize(
        "url",
        [
            "klnzsbguvitpnixnvsqs.supabase.co/auth/v1/.well-known/jwks.json",  # no scheme
            "http://klnzsbguvitpnixnvsqs.supabase.co/auth/v1/.well-known/jwks.json",  # not https
            "https:///auth/v1/.well-known/jwks.json",  # no host
        ],
    )
    def test_non_absolute_https_jwks_url_is_fatal(self, settings, url):
        settings.auth.jwks_url = url
        with pytest.raises(StartupValidationError, match="absolute https URL"):
            validate_settings()

    def test_wrong_jwks_path_warns(self, settings):
        """The dashboard shows several Supabase URLs; pasting the project URL, or dropping
        the /.well-known/ segment, yields a 404 that reaches users as 'invalid token'."""
        settings.auth.jwks_url = "https://klnzsbguvitpnixnvsqs.supabase.co/auth/v1/jwks.json"
        assert any(".well-known" in w for w in validate_settings())

    def test_correct_jwks_url_is_clean(self, settings):
        settings.auth.jwks_url = GOOD_JWKS
        settings.llm.enabled = False
        assert validate_settings() == []

    def test_alg_none_in_the_asymmetric_list_is_fatal(self, settings):
        """jwt_algorithms was already guarded; jwt_asymmetric_algorithms is the list actually
        consulted whenever a JWKS URL is set — i.e. on every current Supabase project."""
        settings.auth.jwks_url = GOOD_JWKS
        settings.auth.jwt_asymmetric_algorithms = ["ES256", "none"]
        with pytest.raises(StartupValidationError, match="ASYMMETRIC"):
            validate_settings()
