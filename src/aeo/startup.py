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
    # DB_POOL_MIN=0 is valid and deliberate on serverless Postgres (Neon, Supabase
    # free): a lazy pool that pre-opens nothing and holds no idle connections.
    if s.database.pool_min < 0:
        fatal.append(f"DB_POOL_MIN must be >= 0 (got {s.database.pool_min})")
    if s.database.pool_max < max(1, s.database.pool_min):
        fatal.append(
            f"DB_POOL_MAX ({s.database.pool_max}) must be >= 1 and >= DB_POOL_MIN "
            f"({s.database.pool_min})"
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


_SAFE_JWT_ALGS = frozenset({"HS256", "HS384", "HS512", "RS256", "ES256"})


def _check_auth(s: Settings, fatal: list[str], warnings: list[str], *, serving: bool) -> None:
    """v5 CH-07 user-auth config. Pure config (no I/O). The algorithm allowlist is the
    load-bearing check: 'none' or an unpinned algorithm reopens the alg=none / RS256→HS256
    forgery class."""
    auth = s.auth
    if auth.jwt_secret is not None and not auth.jwt_secret.strip():
        fatal.append("AEO__AUTH__JWT_SECRET is set but blank — unset it or give it a real value")
    if not auth.jwt_aud.strip():
        fatal.append("AEO__AUTH__JWT_AUD must not be blank")
    bad = [a for a in auth.jwt_algorithms if a.strip().lower() == "none" or a not in _SAFE_JWT_ALGS]
    if bad:
        fatal.append(f"AEO__AUTH__JWT_ALGORITHMS has unsafe/unknown entries: {bad}")
    # The asymmetric list was never checked, yet it is the list that is actually consulted
    # whenever a JWKS URL is configured — i.e. on every Supabase project created since JWT
    # signing keys became the default. 'none' there is the same forgery hole.
    bad_asym = [
        a for a in auth.jwt_asymmetric_algorithms
        if a.strip().lower() == "none" or a not in _SAFE_JWT_ALGS
    ]
    if bad_asym:
        fatal.append(f"AEO__AUTH__JWT_ASYMMETRIC_ALGORITHMS has unsafe/unknown entries: {bad_asym}")
    # A JWKS URL is only ever exercised by a real login, so a typo'd or half-substituted one
    # boots perfectly clean and then 401s every single user — the failure mode this whole
    # deployment already lost hours to. These are shape checks only (no I/O at boot); the
    # network half lives in scripts/check_auth_config.py --live.
    if auth.jwks_url:
        parsed = urlparse(auth.jwks_url)
        if "<" in auth.jwks_url or ">" in auth.jwks_url:
            fatal.append(
                "AEO__AUTH__JWKS_URL still contains a placeholder like <project-ref> — "
                "substitute your real Supabase project ref"
            )
        elif parsed.scheme != "https" or not parsed.netloc:
            fatal.append(
                f"AEO__AUTH__JWKS_URL must be an absolute https URL (got {auth.jwks_url!r})"
            )
        elif not parsed.path.endswith("/.well-known/jwks.json"):
            # Supabase serves the key set at exactly one path; anything else 404s, and a 404
            # is indistinguishable from a forged token by the time it reaches a user.
            warnings.append(
                f"AEO__AUTH__JWKS_URL path is {parsed.path!r} — Supabase serves its key set at "
                "/auth/v1/.well-known/jwks.json; a wrong path 401s every login"
            )
    # Must mirror api.auth.auth_active(): EITHER credential activates verification. Checking
    # only jwt_secret told correctly-configured JWKS deployments (the Supabase default, which
    # has no shared secret at all) that auth was disabled — a false alarm that trains you to
    # ignore the warning that matters.
    auth_active = auth.enabled and bool(auth.jwt_secret or auth.jwks_url)
    if serving and not auth_active:
        warnings.append(
            "serving without AEO__AUTH__JWT_SECRET or AEO__AUTH__JWKS_URL — deep-value routes "
            "(pack detail, per-user unlocks) are open and bind to a shared dev user; set one "
            "in any public deployment"
        )
    # Prod-posture mismatch: the service key is set but user auth is off.
    if serving and s.api.auth_key and not auth_active:
        warnings.append(
            "AEO__API__AUTH_KEY is set but user auth is disabled — deep-value routes have no "
            "per-user gate (shared dev user); set AEO__AUTH__JWT_SECRET or AEO__AUTH__JWKS_URL"
        )
    # The service key is NOT an authorization boundary: the web proxy injects it into every
    # forwarded request, so any visitor's browser presents it. Without a distinct admin key
    # the entitlement-minting routes refuse to serve (require_admin_key fails closed) — say
    # so at boot rather than letting an operator discover it via a 503.
    if serving and s.api.auth_key and not s.api.admin_key:
        warnings.append(
            "AEO__API__ADMIN_KEY is unset — admin routes (/api/entitlements/grant) are "
            "DISABLED. Set it to issue manual grants; never reuse AEO__API__AUTH_KEY, which "
            "the web proxy hands to every visitor"
        )


def _check_payments(s: Settings, fatal: list[str], warnings: list[str], *, serving: bool) -> None:
    """v5 CH-02b Stripe config. Pure config (no I/O).

    The fatal case is money-losing and otherwise silent: a secret key WITHOUT a webhook
    secret means checkout succeeds, customers are charged, and every webhook delivery fails
    signature verification — so no entitlement is ever written and Stripe gives up retrying
    after ~3 days. Refuse to boot rather than sell something that cannot be delivered."""
    pay = s.payments
    if not pay.enabled or not pay.stripe_secret_key:
        return
    if not pay.webhook_secret:
        fatal.append(
            "AEO__PAYMENTS__STRIPE_SECRET_KEY is set without AEO__PAYMENTS__WEBHOOK_SECRET — "
            "customers would be charged and never granted their pack (every webhook would be "
            "rejected). Set the signing secret from the Stripe endpoint, or unset the key"
        )
    if pay.pack_price_cents <= 0 and not pay.stripe_price_id:
        fatal.append("AEO__PAYMENTS__PACK_PRICE_CENTS must be > 0 (or set a STRIPE_PRICE_ID)")
    if serving and not pay.public_app_url:
        warnings.append(
            "AEO__PAYMENTS__PUBLIC_APP_URL is unset — Stripe will return buyers to the API's "
            "own origin, which serves no /studio. Set it to the web app's public URL"
        )
    if serving and pay.stripe_secret_key.startswith("sk_live_") and not s.api.auth_key:
        warnings.append("LIVE Stripe key with no AEO__API__AUTH_KEY — the API is unauthenticated")


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
    _check_auth(s, fatal, warnings, serving=serving)
    _check_payments(s, fatal, warnings, serving=serving)

    for w in warnings:
        log.warning("startup_config_warning", detail=w)
    if fatal:
        for f in fatal:
            log.error("startup_config_fatal", detail=f)
        raise StartupValidationError(fatal)

    log.info("startup_config_ok", warnings=len(warnings), llm_enabled=s.llm.enabled,
             llm_provider=s.llm.provider if s.llm.enabled else None)
    return warnings
