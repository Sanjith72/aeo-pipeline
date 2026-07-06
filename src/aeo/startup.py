"""
Startup validation: fail fast on broken configuration, warn loudly on degraded.

Called from the CLI bootstrap (every ``aeo`` command) and the FastAPI lifespan, so a
misconfigured deployment dies at boot with an actionable message instead of 500ing on
the first request.

Two severities, matching the codebase's deterministic-first contract:

  - **fatal** — configuration that can never work (malformed DATABASE_URL, unknown LLM
    provider, nonsensical numbers). Raises :class:`StartupValidationError`.
  - **warning** — configuration that works but degrades (no LLM key → deterministic
    mode, public API without an auth key, Supabase direct host without SSL). Logged as
    structured warnings and returned for callers that want to surface them.

Pure config checks only — no network or DB I/O happens here.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .logging import get_logger
from .settings import KNOWN_LLM_PROVIDERS, Settings, get_settings

log = get_logger(__name__)


class StartupValidationError(RuntimeError):
    """Raised when settings contain at least one fatal problem."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        bullet_list = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"invalid configuration ({len(problems)} problem(s)):\n{bullet_list}")


def _check_database(s: Settings, fatal: list[str], warnings: list[str]) -> None:
    url = s.database.url
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        fatal.append(
            f"DATABASE_URL must start with postgresql:// — got scheme {parsed.scheme!r}"
        )
        return
    if not parsed.hostname:
        fatal.append("DATABASE_URL has no host")
    # The URL now reaches libpq verbatim (sslmode etc. survive) — so an unknown query
    # param or malformed DSN would abort the FIRST connection at request time. Parse it
    # with libpq's own rules here (pure parsing, no I/O) to fail at boot instead.
    try:
        from psycopg2.extensions import parse_dsn

        parse_dsn(url)
    except Exception as exc:
        fatal.append(f"DATABASE_URL is not a valid libpq DSN: {exc}")
    if s.database.pool_min < 1:
        fatal.append(f"DB_POOL_MIN must be >= 1 (got {s.database.pool_min})")
    if s.database.pool_max < s.database.pool_min:
        fatal.append(
            f"DB_POOL_MAX ({s.database.pool_max}) must be >= DB_POOL_MIN ({s.database.pool_min})"
        )

    host = parsed.hostname or ""
    query = parse_qs(parsed.query)
    import os

    has_ssl = "sslmode" in query or bool(os.getenv("PGSSLMODE"))
    is_hosted = host.endswith((".supabase.co", ".supabase.com", ".neon.tech"))
    if is_hosted and not has_ssl:
        warnings.append(
            "hosted Postgres URL without sslmode — append ?sslmode=require to DATABASE_URL "
            "(or set PGSSLMODE=require)"
        )
    if host.startswith("db.") and host.endswith(".supabase.co"):
        warnings.append(
            "Supabase DIRECT host (db.*.supabase.co) is IPv6-only on the free plan — most "
            "container hosts need the Supavisor pooler host instead "
            "(aws-*-*.pooler.supabase.com:6543, transaction mode)"
        )


def _check_llm(s: Settings, fatal: list[str], warnings: list[str]) -> None:
    llm = s.llm
    for field, value in (
        ("provider", llm.provider),
        ("bulk_provider", llm.bulk_provider),
        ("planning_provider", llm.planning_provider),
    ):
        if value and value not in KNOWN_LLM_PROVIDERS:
            fatal.append(
                f"AEO__LLM__{field.upper()} {value!r} is not one of {sorted(KNOWN_LLM_PROVIDERS)}"
            )
    if llm.timeout_sec <= 0 or llm.interactive_timeout_sec <= 0:
        fatal.append("AEO__LLM__TIMEOUT_SEC and AEO__LLM__INTERACTIVE_TIMEOUT_SEC must be > 0")
    if not 0.0 <= llm.temperature <= 2.0:
        fatal.append(f"AEO__LLM__TEMPERATURE must be within [0, 2] (got {llm.temperature})")

    if not llm.enabled:
        return
    providers_in_use = {p for p in (llm.provider, llm.bulk_provider, llm.planning_provider) if p}
    missing: list[str] = []
    if "cloud" in providers_in_use and not llm.cloud_api_key:
        missing.append("AEO__LLM__CLOUD_API_KEY (provider 'cloud')")
    if providers_in_use & {"gemini", "hybrid"} and not llm.gemini_api_key:
        missing.append("AEO__LLM__GEMINI_API_KEY (provider 'gemini'/'hybrid')")
    if providers_in_use & {"qwen", "hybrid"} and not llm.qwen_api_key:
        missing.append("AEO__LLM__QWEN_API_KEY (provider 'qwen'/'hybrid')")
    for m in missing:
        warnings.append(f"{m} is unset — those calls degrade to fallback/deterministic output")


def _check_agents(s: Settings, fatal: list[str]) -> None:
    if s.agents.mode not in ("react", "ladder"):
        fatal.append(f"AEO__AGENTS__MODE must be 'react' or 'ladder' (got {s.agents.mode!r})")
    if s.agents.react_max_steps < 1 or s.agents.step_timeout_sec <= 0:
        fatal.append("AEO__AGENTS__REACT_MAX_STEPS must be >= 1 and STEP_TIMEOUT_SEC > 0")


def _check_api(s: Settings, fatal: list[str], warnings: list[str], *, serving: bool) -> None:
    api = s.api
    if api.auth_key is not None and not api.auth_key.strip():
        fatal.append("AEO__API__AUTH_KEY is set but blank — unset it or give it a real value")
    if api.rate_limit < 0 or api.rate_window_sec <= 0:
        fatal.append("AEO__API__RATE_LIMIT must be >= 0 and AEO__API__RATE_WINDOW_SEC > 0")
    if serving and not api.auth_key:
        warnings.append(
            "serving without AEO__API__AUTH_KEY — every /api/* route is unauthenticated; "
            "set it in any public deployment"
        )
    if serving and api.rate_limit == 0:
        warnings.append(
            "serving without a rate limit (AEO__API__RATE_LIMIT=0) — fine locally, set it "
            "in any public deployment"
        )


def validate_settings(*, serving: bool = False) -> list[str]:
    """Validate settings; raise :class:`StartupValidationError` on fatal problems.

    Returns the (possibly empty) list of warnings, which are also logged. ``serving``
    enables the checks that only matter when the HTTP API is exposed.
    """
    s = get_settings()
    fatal: list[str] = []
    warnings: list[str] = []

    _check_database(s, fatal, warnings)
    _check_llm(s, fatal, warnings)
    _check_agents(s, fatal)
    _check_api(s, fatal, warnings, serving=serving)

    for w in warnings:
        log.warning("startup_config_warning", detail=w)
    if fatal:
        for f in fatal:
            log.error("startup_config_fatal", detail=f)
        raise StartupValidationError(fatal)

    log.info("startup_config_ok", warnings=len(warnings), llm_enabled=s.llm.enabled,
             llm_provider=s.llm.provider if s.llm.enabled else None)
    return warnings
