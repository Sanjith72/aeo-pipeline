"""
Application settings.

Three-layer config, merged in this order (later wins):
  1. Defaults in code
  2. config/*.yaml files
  3. Environment variables (AEO__SECTION__KEY=value)

Anything secret (DB URL, API keys) belongs in env vars only.
"""

from __future__ import annotations

import os
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


# ---------------------------------------------------------------------------
# YAML-backed sections
# ---------------------------------------------------------------------------


class RateLimitCfg(BaseModel):
    requests_per_minute: int = 30
    burst: int = 5


class RetryCfg(BaseModel):
    max_attempts: int = 4
    initial_backoff_sec: float = 1.5
    max_backoff_sec: float = 30.0
    retry_on_status: list[int] = Field(default_factory=lambda: [408, 425, 429, 500, 502, 503, 504])


class FingerprintCfg(BaseModel):
    enabled: bool = True
    algorithm: str = "sha256"


class BrowserCfg(BaseModel):
    headless: bool = True
    remove_overlay_elements: bool = True
    word_count_threshold: int = 0


class DiscoveryCfg(BaseModel):
    # Site Discovery harvests a domain's URL inventory (sitemap, then recursive)
    # with plain HTTP GETs — no JS render needed to read links. max_urls caps the
    # inventory *before* prioritization (which then cuts to prioritization.top_n).
    max_urls: int = 200
    max_depth: int = 2          # recursive fallback BFS depth from the homepage
    max_sitemaps: int = 50      # cap sitemap-index expansion (avoid pathological sites)
    timeout_sec: int = 15


class CrawlerCfg(BaseModel):
    user_agent: str = "AEOBot/0.2"
    concurrency: int = 4
    request_timeout_sec: int = 30
    respect_robots: bool = True
    # Force IPv4 on outbound HTTP. On OCI Ampere (ARM) the default dual-stack
    # resolver silently stalls scraper fetches on AAAA records; binding the
    # client to an IPv4 local address (0.0.0.0) forces AF_INET. Off by default
    # (dev/most clouds are fine); flip on in the OCI deployment.
    force_ipv4: bool = False
    rate_limit: RateLimitCfg = RateLimitCfg()
    retry: RetryCfg = RetryCfg()
    fingerprint: FingerprintCfg = FingerprintCfg()
    browser: BrowserCfg = BrowserCfg()
    discovery: DiscoveryCfg = DiscoveryCfg()


# Every provider the LLM layer can build a backend for. "hybrid" is the Gemini+Qwen
# router with automatic failover; "cloud" is the legacy single OpenAI-compatible endpoint.
KNOWN_LLM_PROVIDERS = frozenset({"ollama", "cloud", "gemini", "qwen", "hybrid"})


class LLMCfg(BaseModel):
    enabled: bool = True          # off → scorers fall back to deterministic-only
    provider: str = "ollama"      # see KNOWN_LLM_PROVIDERS; "hybrid" = Gemini+Qwen router
    # Hybrid routing: the BURST path — the async deep audit's per-page scoring/analysis fires
    # dozens of calls and trips cloud free-tier rate limits. Set AEO__LLM__BULK_PROVIDER=ollama
    # to run it on the local model (slower but un-throttled; fine since the audit is async)
    # while the fast synchronous endpoints stay on the primary `provider`. Empty = use primary.
    bulk_provider: str = ""
    # Hybrid reasoning tier: the agent Planner/Builder route their frontier calls here (e.g.
    # AEO__LLM__PLANNING_PROVIDER=cloud) while the bulk audit path stays on bulk_provider and
    # the fast sync endpoints stay on `provider`. Empty = use the primary `provider`.
    planning_provider: str = ""
    # Ollama (local) backend
    host: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    # Cloud backend: any OpenAI-compatible /chat/completions endpoint (OpenAI,
    # Gemini's compat endpoint, Together, …). Key via AEO__LLM__CLOUD_API_KEY.
    cloud_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    cloud_model: str = "gemini-2.5-flash"
    cloud_api_key: str | None = None
    # ── Hybrid Gemini + Qwen (provider="hybrid", or pin one with "gemini"/"qwen") ──
    # The router sends REASONING-profile calls (planning, blueprint synthesis, agent
    # steps) to Gemini and FAST-profile calls (per-page scoring refinement, drafting,
    # classification) to Qwen, then fails over — first in-family (flash → flash-lite,
    # Groq → OpenRouter), then across families — on 429/5xx/timeouts/malformed JSON.
    # Every endpoint speaks OpenAI /chat/completions. Defaults target the $0 tiers
    # (verified 2026-07): Gemini via AI Studio keys from a NO-billing project
    # (gemini-2.5-flash ≈10 RPM/250 req-day free; flash-lite ≈15 RPM/1000 req-day),
    # Qwen primarily via Groq's free plan (qwen3-32b: 60 RPM/1000 req-day/6K TPM —
    # keep prompts short), with OpenRouter ':free' (20 RPM/50 req-day at strictly $0)
    # as the in-family fallback. NOTE: ':free' model ids churn within months — if
    # OpenRouter 404s the model, check https://openrouter.ai/api/v1/models.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_fallback_model: str = "gemini-2.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    qwen_api_key: str | None = None
    qwen_model: str = "qwen/qwen3-32b"
    qwen_base_url: str = "https://api.groq.com/openai/v1"
    qwen_fallback_api_key: str | None = None
    qwen_fallback_model: str = "qwen/qwen3-next-80b-a3b-instruct:free"
    qwen_fallback_base_url: str = "https://openrouter.ai/api/v1"
    # Failover/retry policy for the gemini/qwen/hybrid backends. Retries apply to
    # RETRYABLE failures (429/5xx/timeouts) with exponential backoff; after the retry
    # budget the hybrid router switches providers instead of failing the call.
    retry_max_attempts: int = 3
    retry_initial_backoff_sec: float = 1.0
    retry_max_backoff_sec: float = 8.0
    failover_enabled: bool = True
    # Embeddings for the pgvector semantic-search substrate (migration 0025). Served by
    # whichever configured provider exposes /embeddings — in practice Gemini, whose free
    # tier includes gemini-embedding-001 (768 dims via the OpenAI-compat `dimensions`).
    embedding_model: str = "gemini-embedding-001"
    # Index each audited page's title/description/headings into content_embeddings
    # during the deep audit (skipped for unchanged content via the sha check). Off by
    # default: it adds one embeddings API call per changed page, which is real quota on
    # the $0 tiers. Semantic search degrades to an honest "unavailable" when off.
    embedding_indexing: bool = False
    # Shared generation params
    timeout_sec: int = 120
    temperature: float = 0.1
    num_predict: int = 600
    # ── Foreground (latency-sensitive) LLM work — fail-fast, distinct from bulk/audit ──
    # The deep audit tolerates a slow local model (fire-and-forget batch on the bulk client).
    # Foreground work where a user is waiting must NOT: the synchronous /api/plan brief→blueprint
    # call, and the /api/deliverables/personalize build whose result is polled.
    #   - interactive_timeout_sec: short, fail-fast per-generation timeout for the foreground
    #     client (`get_interactive_client`), distinct from the bulk/audit `timeout_sec` above.
    #     One try, no retry; used by /api/plan and the personalize build.
    # The page-DRAFT fan-out (up to draft_limit pages) gets two more bounds on top:
    #   - draft_concurrency: bounded worker pool for the concurrent draft phase.
    #   - draft_phase_budget_sec: total wall-clock budget for the whole draft phase; once spent,
    #     the not-yet-drafted pages fall back to the deterministic scaffold at once instead of
    #     each waiting out its own timeout.
    interactive_timeout_sec: int = 45
    draft_concurrency: int = 4
    draft_phase_budget_sec: int = 180

    @field_validator("provider", "bulk_provider", "planning_provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: object) -> object:
        # "Ollama" / " cloud " and friends worked before strict validation existed;
        # normalize so startup validation and _make_backend agree on the same token.
        return v.strip().lower() if isinstance(v, str) else v


class DatabaseCfg(BaseModel):
    url: str = "postgresql://aeo:aeo@localhost:5432/aeo"
    pool_min: int = 2
    pool_max: int = 10


class ValidationCfg(BaseModel):
    # Validation loop: apply the proposed edits to a synthetic page, re-score,
    # and retry the Recommender if the simulated score did not improve. Capped so
    # a stubborn page can never spin forever — after this many attempts it is
    # flagged 'could-not-improve' and routed to Human Review.
    max_attempts: int = 3
    # v4 Independent Validator: after the edit-efficacy gate, run independent,
    # non-circular signals (deterministic checks + Perplexity citation test) to
    # decide review routing. Off → the v3 (circular) re-score gate alone decides.
    independent_enabled: bool = True
    # Fan-out for the per-page analysis loop (gap→recommend→validate→report).
    # Each page is independent and Error-Sink isolated, so they run in a thread
    # pool. 1 = sequential (the v3 behavior). Real win when an LLM is enabled.
    analysis_concurrency: int = 1
    # v4+ Adversarial auditor (ported idea): an independent, model-isolated skeptic
    # that tries to REFUTE each recommendation, plus deterministic citation-
    # hallucination checks on any URLs it cites. Off by default; when on but the LLM
    # is disabled it degrades to the deterministic citation checks alone (never
    # fails a page for a missing model — the deterministic-first contract).
    adversarial_enabled: bool = False
    adversarial_max_attempts: int = 3
    # Verify cited URLs are actually reachable (a HEAD request per citation). Off by
    # default since it makes outbound network calls; structural validity is always checked.
    verify_citations: bool = False


class RetentionCfg(BaseModel):
    # Retention Engine (#11): on every re-crawl, check whether the watched page
    # changed since we issued a recommendation against it, and flip the pending
    # outcome to 'implemented'. This is detection bookkeeping, kept SEPARATE from the
    # fingerprint skip-for-cost path — a watched page that changed is the most
    # valuable event in the system and must never be silently skipped. Best-effort:
    # a hiccup here is logged and never aborts a crawl/analysis run.
    enabled: bool = True


class MilestonesCfg(BaseModel):
    # Implementation Milestones: the "Final Plan" persisted per client + verified by the
    # weekly crawl. When `verify_on_crawl` is on, the audit cycle re-scrapes the site and
    # auto-flips any milestone task whose recommended artifact (a page slug / offering /
    # heading) is now live to 'verified_completed'. Best-effort: a hiccup here never
    # aborts the audit. Off → milestones still persist + track, but only the owner's
    # manual toggles advance them.
    verify_on_crawl: bool = True
    # v5 CH-15 before/after: when a v5 skill TICKET is closed (closed_pending_verify), a
    # forced re-crawl re-scores the page; this flips the ticket to verified_completed and
    # pins current_score. Separate from verify_on_crawl (artifact presence) and the
    # retention engine (criterion tiers) — its own table (milestone_tasks skill columns).
    verify_tickets_on_crawl: bool = True
    # Honest lift gate: only mark a closed ticket verified when the re-scored skill score
    # is >= the pinned baseline (the fix demonstrably didn't regress it). False → flip on
    # any re-score. Either way current_score is recorded so the UI can show the delta.
    verify_require_lift: bool = True


class IntakeCfg(BaseModel):
    # Intake intelligence (#3): branch on crawl quality so the URL can be the only
    # input. A site with fewer than `thin_site_min_pages` pages (or, when body text is
    # available, fewer than `thin_site_min_words` words) is too thin to audit
    # meaningfully and is routed to the brief/build path instead; a crawl that finds
    # nothing falls through to the no-website path. Tunable via AEO__INTAKE__*.
    enabled: bool = True
    thin_site_min_pages: int = 5
    thin_site_min_words: int = 300
    # When the fast httpx prefill fetch is blocked (403 / JS bot-challenge stub) on a site
    # whose wall a real browser gets past, fall back to a headless-Chromium render of the
    # HOMEPAGE ONLY (industry/offer live there). Costs ~6-8s, so it fires only on the
    # minority of blocked sites; the common case never pays it. Set AEO__INTAKE__
    # PLAYWRIGHT_FALLBACK=false to disable (e.g. hosts without the browser installed).
    playwright_fallback: bool = True
    playwright_timeout_sec: int = 18
    # Max concurrent headless renders off the shared browser pool (crawl.browser_pool). One
    # browser process is reused; this caps simultaneous contexts so a burst of blocked-site
    # prefills can't exhaust memory. Small by default — the fallback is rare.
    playwright_pool_size: int = 2


class PerplexityCfg(BaseModel):
    # The v4 Independent Validator's real-world signal: query the target question
    # on Perplexity and compare the rewrite's shape to what's actually cited.
    # Disabled by default — with no key the validator falls back to its
    # deterministic independent checks (never fails a page for a missing key).
    enabled: bool = False
    api_key: str | None = None
    base_url: str = "https://api.perplexity.ai"
    model: str = "sonar"
    timeout_sec: int = 60


class ScoringCfg(BaseModel):
    # v4 Parallel Processor: run the criterion scorers concurrently in a thread
    # pool. Output is identical to sequential (scorers are pure over a shared
    # read-only context); the win is on the I/O-bound LLM-refined criteria.
    parallel: bool = False
    max_workers: int = 8
    # v5 CH-04: compute + persist the five-skill derived layer on each scored page during
    # the deep audit. When on (default), the net-new Messaging/Conversion skills are
    # LLM-judged (2 extra model calls per page — set AEO__SCORING__SKILL_LLM=false on
    # quota-tight free-tier hosts to keep them deterministic/provisional instead). The
    # mapped skills (Discovery/Proof/Structure) are always free — derived from criteria.
    skills_enabled: bool = True
    skill_llm: bool = True
    # v5 CH-14: max AI-visibility probes per RUN in the deep audit ("each analyzed page",
    # but bounded). Costs nothing in the default deployment — Perplexity is off, so every
    # page short-circuits to 'unavailable' with no network and no score change. Only real
    # engine calls consume the budget; pages past it report 'budget_exhausted' rather than
    # a guess. 0 disables per-page AI visibility entirely (the free overview is unaffected).
    ai_visibility_max_pages: int = 10


class AgentsCfg(BaseModel):
    # Phase 2 agent runtime: scoped LLM agents driven by a deterministic controller on the
    # existing Postgres job queue (no new broker). concurrency caps how many runs may be in
    # flight (queued/planning) at once — POST /api/agent/run answers 429 past it;
    # step_timeout_sec bounds a single agent step (Phase 2B enforces it per-LLM-call);
    # max_attempts is the per-run job retry budget before the queue marks it dead.
    concurrency: int = 4
    step_timeout_sec: int = 120
    max_attempts: int = 3
    # Orchestration mode. "react": the agentic loop — the LLM plans its own steps over the
    # tool registry (agents/tools.py), self-corrects on observations, and stages when ready;
    # falls back to the ladder automatically when no LLM is available or the loop can't
    # stage a plan. "ladder": the fixed research → plan → build → critic sequence.
    mode: str = "react"
    react_max_steps: int = 12
    # Phase 2B agent steps (each has a deterministic floor, so disabling only skips the LLM work).
    research_enabled: bool = True   # discover + live-verify competitors before planning
    build_enabled: bool = True      # draft staged page copy after planning
    draft_limit: int = 5            # cap drafts per run (the dominant frontier cost)
    critic_enabled: bool = True     # gate staged drafts (independent + adversarial + claim audit) before human review


class ObsCfg(BaseModel):
    # Observability. The custom ``agent_traces`` table + ``aeo trace`` are always on
    # (queryable per-page journey). This adds OPTIONAL OpenTelemetry OTLP export
    # ALONGSIDE it (ported idea) — standards-aligned distributed tracing for an OTLP
    # collector (Tempo/Jaeger/Honeycomb). Off by default; a no-op when the SDK isn't
    # installed or no endpoint is set, so it never adds a hard dependency.
    otel_enabled: bool = False
    otel_endpoint: str = ""  # OTLP gRPC endpoint, e.g. http://localhost:4317
    otel_service_name: str = "aeo"


class ApiCfg(BaseModel):
    # HTTP API (SP-4) auth. When AEO__API__AUTH_KEY is set, every /api/* route except
    # /api/health requires a matching X-API-Key header. Unset (default) = open mode for
    # local dev; set it in any deployment that exposes the API.
    auth_key: str | None = None
    # ADMIN credential for the handful of routes that can mint entitlements or read another
    # user's data (X-Admin-Key). It MUST be distinct from auth_key: the web app's server-side
    # proxy (web/app/api/[...path]/route.ts) injects auth_key into every /api/* request it
    # forwards, so any visitor's browser can present it. auth_key authenticates the PROXY,
    # not the person — it is not an authorization boundary. Unset + auth_key set → the admin
    # routes are DISABLED (fail closed), because "open admin route in a deployment" hands out
    # free entitlements. Unset + auth_key unset → fully-open local dev, allowed.
    admin_key: str | None = None
    # The explicit "yes, I really mean to run this wide open" switch. Serving WITHOUT an
    # auth_key is a fatal startup error (startup._check_api), because with neither key set
    # require_admin_key returns None and /api/entitlements/grant is completely ungated —
    # anyone who can reach the backend's own URL mints themselves all_packs. That is not a
    # posture any deployment should be able to reach by forgetting a variable, so the only
    # way to it is naming it: AEO__API__ALLOW_OPEN=1 (scripts/run.ps1 and the compose stack
    # set it, since both are localhost-only). Never set it on a public host.
    allow_open: bool = False
    # Browser origins allowed to call the API (the SP-4b web UI runs on another port,
    # so every fetch is cross-origin). Comma-separated via AEO__API__CORS_ORIGINS.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Per-client-IP rate limit on /api/* (except /api/health): at most `rate_limit` requests
    # per `rate_window_sec`. 0 = disabled (the local-dev default); set AEO__API__RATE_LIMIT in
    # any public deployment. Client IP = left-most X-Forwarded-For (behind a proxy) or the peer.
    rate_limit: int = 0
    rate_window_sec: int = 60
    # v5 §9.4 free-tier cap: fresh (non-cached) POST /api/overview builds per IP per day.
    # 0 = disabled for local dev; set AEO__API__OVERVIEW_DAILY_LIMIT (≈3) in any public
    # deployment. Cached hits never count — same-domain re-pastes stay free. The per-IP
    # key is spoofable via X-Forwarded-For, so pair it with the global ceiling below.
    overview_daily_limit: int = 0
    # Global daily ceiling on fresh overview builds across ALL callers — the backstop no
    # single-IP spoofing trick can bypass. 0 = disabled; set it comfortably above expected
    # honest daily volume in any public deployment.
    overview_global_daily_limit: int = 0

    @field_validator("auth_key", "admin_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:
        """Treat an empty/whitespace env value as UNSET — the same rule AuthCfg applies to
        its credentials, for the same reason: `AEO__API__AUTH_KEY=` in a .env reads as "not
        configured" to a human, so the config should agree with them.

        This used to be a distinct fatal ("set but blank — unset it or give it a real
        value"), which made blank and unset behave differently for no benefit. It is not a
        loosening: serving with no key at all is now itself fatal, so the operator who typed
        a bare `AEO__API__AUTH_KEY=` still cannot boot a public API — they just get the
        message that tells them what to do about it instead of one about whitespace."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class AuthCfg(BaseModel):
    # v5 CH-07 Supabase-JWT user auth — a SEPARATE credential/boundary from ApiCfg.auth_key
    # (which is the service proxy→backend key). This gates per-USER deep value (pack detail,
    # per-user unlocks). Stateless HS256 verification against the Supabase project JWT secret
    # (AEO__AUTH__JWT_SECRET) — the backend never calls Supabase, so it works with a Neon DB.
    # Degrades to disabled/open when the secret is unset, exactly like auth_key.
    enabled: bool = True
    # The Supabase project JWT secret (Settings → API → JWT Secret). HS256 sign==verify key:
    # treat as a SIGNING key — env-only, never logged. Unset → auth inactive (dev/open),
    # UNLESS jwks_url is set (asymmetric projects have no shared secret to configure).
    jwt_secret: str | None = None
    # Asymmetric verification (ES256/RS256). Supabase projects created with JWT *signing
    # keys* — the current default — do NOT expose a shared HS256 secret, so the secret-only
    # path silently rejects every real login. Point this at the project's JWKS endpoint
    # (https://<ref>.supabase.co/auth/v1/.well-known/jwks.json) and the verifier fetches +
    # caches the public keys and picks the key by the token's `kid`. Private keys never
    # leave Supabase. Set EITHER this or jwt_secret; both is fine during a key rotation.
    jwks_url: str | None = None
    jwks_cache_sec: int = 600  # PyJWKClient lifespan — bounds how long a rotated-out key lingers
    # Supabase access tokens carry aud="authenticated"; the anon/service_role keys (also JWTs
    # signed with the SAME secret) do NOT — this + the role check is what blocks them.
    jwt_aud: str = "authenticated"
    # Pinned explicitly so alg=none is impossible. HS256 for a shared-secret project; the
    # asymmetric algs are only ever ACCEPTED when jwks_url is configured (see
    # api/auth.py::_algorithms) — a secret-only deployment stays HS256-only, so a public
    # key can never be smuggled in as an HMAC secret (the classic RS256→HS256 confusion).
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["HS256"])
    jwt_asymmetric_algorithms: list[str] = Field(default_factory=lambda: ["ES256", "RS256"])
    # Optional issuer pin (https://<ref>.supabase.co/auth/v1) — defends against a token minted
    # for a different project with the same secret. Off unless configured.
    jwt_issuer: str | None = None
    leeway_sec: int = 10
    # Comma-separated promo codes that redeem to an all_packs grant (v5 monetization stub —
    # payments deferred; grants arrive via source='promo'). Empty → redemption disabled.
    promo_codes: str = ""
    # Disabled-mode stand-in so pack-detail routes stay reachable in local dev with no secret.
    dev_user_id: str = "00000000-0000-0000-0000-000000000000"

    @field_validator("jwt_secret", "jwks_url", "jwt_issuer", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:
        """Treat an empty/whitespace env value as UNSET. Writing `AEO__AUTH__JWT_ISSUER=`
        in a .env reads as "not configured" to a human, but an empty string is truthy
        enough to reach jwt.decode(issuer="") — which then rejects EVERY token, with the
        misleading 'invalid token' 401. Same trap for a blank secret/JWKS URL."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def promo_code_set(self) -> frozenset[str]:
        return frozenset(c.strip() for c in self.promo_codes.split(",") if c.strip())


class PaymentsCfg(BaseModel):
    """v5 CH-02b — Stripe Checkout, flat price per pack (§9.2's open pricing decision,
    resolved). Unset → the buy path 503s and the UI hides it; promo codes and manual grants
    still work, so an unconfigured deployment degrades to the documented stub."""

    enabled: bool = True
    # Stripe SECRET key (sk_live_… / sk_test_…). Env-only, never logged, never sent to the
    # browser — the publishable key is not needed here because Checkout is a redirect.
    stripe_secret_key: str | None = None
    # Webhook signing secret (whsec_…) from the endpoint's Stripe dashboard page. This is
    # the ONLY credential on /api/webhooks/stripe, which is exempt from the X-API-Key guard.
    webhook_secret: str | None = None
    # Flat price for ONE pack, in the currency's minor unit (4900 = $49.00).
    pack_price_cents: int = 4900
    currency: str = "usd"
    # Optional dashboard-managed Price id; overrides pack_price_cents/currency when set.
    stripe_price_id: str | None = None
    # The PUBLIC WEB APP origin Stripe returns the buyer to (e.g. https://app.example.com).
    # This must be set in any real deployment. It cannot be derived from the request: the
    # browser talks to the Next.js proxy, which rewrites Host before forwarding, so
    # request.base_url is the BACKEND's origin (http://api:8000, or the Railway API host).
    # Building success_url from that sent the paying customer to a backend 404 — the API
    # serves no /studio. Unset → fall back to the request origin, which is correct only when
    # the API and the UI are the same origin (local dev).
    public_app_url: str | None = None
    # Where Stripe returns the buyer, joined onto public_app_url.
    success_path: str = "/studio?checkout=success"
    cancel_path: str = "/studio?checkout=cancelled"
    request_timeout_sec: float = 20.0

    @field_validator("stripe_secret_key", "webhook_secret", "stripe_price_id", "public_app_url", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:
        """Blank env value = unset (mirrors AuthCfg) — so a stray `AEO__PAYMENTS__…=` in a
        .env reads as "not configured" instead of an empty credential that fails oddly."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ReferenceArchitectureCfg(BaseModel):
    # v4 Reference Architecture Generator: the versioned, per-topic ideal-site
    # blueprint. Generated on a slow cadence and pinned per run so the measuring
    # stick doesn't move week to week.
    enabled: bool = True
    topic: str = "PEV"  # default topic (Proactive Exposure / Vulnerability mgmt) for Securin
    framework_version: str = "1"
    min_pages_per_cluster: int = 10  # topical-authority target; thin-cluster threshold
    regenerate_cadence_days: int = 30  # regenerate only this often (else reuse pinned version)
    # Per-engine emphasis for LLM blueprint synthesis (ported idea): perplexity →
    # citation density; chatgpt_search → conversational coverage; gemini → entity
    # structure; generic → engine-neutral. Routes the synthesis PROMPT only — the
    # deterministic floor and the closed-vocab guardrail are unchanged. Unknown
    # values fall back to 'generic'.
    engine_target: str = "generic"
    # The generator uses the configured LLM (set llm.provider=cloud pointed at
    # Gemini's OpenAI-compatible endpoint) for synthesis; falls back to the
    # deterministic builder when the LLM is disabled or fails.
    # v4+ Content drafting: turn missing-page coverage gaps into ready-to-publish
    # drafts (H1 + headers + body prose + JSON-LD) attached to the site report. The
    # top `draft_limit` missing pages (priority order) are drafted — LLM-authored
    # prose when enabled, a deterministic scaffold otherwise. 0 → draft none.
    draft_missing_pages: bool = True
    draft_limit: int = 10


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="AEO__",
        extra="ignore",
    )

    crawler: CrawlerCfg = CrawlerCfg()
    llm: LLMCfg = LLMCfg()
    database: DatabaseCfg = DatabaseCfg()
    validation: ValidationCfg = ValidationCfg()
    retention: RetentionCfg = RetentionCfg()
    milestones: MilestonesCfg = MilestonesCfg()
    intake: IntakeCfg = IntakeCfg()
    perplexity: PerplexityCfg = PerplexityCfg()
    scoring: ScoringCfg = ScoringCfg()
    agents: AgentsCfg = AgentsCfg()
    reference_architecture: ReferenceArchitectureCfg = ReferenceArchitectureCfg()
    obs: ObsCfg = ObsCfg()
    api: ApiCfg = ApiCfg()
    auth: AuthCfg = AuthCfg()
    payments: PaymentsCfg = PaymentsCfg()

    log_level: str = "INFO"
    log_format: str = "console"
    config_dir: str = str(DEFAULT_CONFIG_DIR)
    psi_api_key: str | None = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Load .env into the process environment BEFORE constructing Settings or
    # reading os.getenv below. pydantic-settings' own env_file parsing only
    # populates AEO__-prefixed model fields; the unprefixed DATABASE_URL /
    # DB_POOL_* contract (read via os.getenv) needs the values in os.environ.
    # override=False keeps real OS env vars winning over .env (prod-safe).
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    s = Settings()
    cfg_dir = Path(s.config_dir)

    # Merge YAML on top of defaults — env vars override YAML below
    crawler_yaml = _load_yaml(cfg_dir / "crawler.yaml")
    if crawler_yaml:
        s.crawler = CrawlerCfg(**{**s.crawler.model_dump(), **crawler_yaml})

    # Re-apply env overrides on the now-merged crawler section
    env_overrides = {k: v for k, v in os.environ.items() if k.startswith("AEO__CRAWLER__")}
    for env_key, env_val in env_overrides.items():
        path = env_key.removeprefix("AEO__CRAWLER__").lower().split("__")
        _set_nested(s.crawler, path, env_val)

    # Database URL & pool from a single DATABASE_URL — keeps the legacy contract
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        s.database = DatabaseCfg(
            url=db_url,
            pool_min=int(os.getenv("DB_POOL_MIN", str(s.database.pool_min))),
            pool_max=int(os.getenv("DB_POOL_MAX", str(s.database.pool_max))),
        )

    return s


def _set_nested(obj: BaseModel, path: list[str], value: str) -> None:
    """Walk a dotted attribute path and set the final value, coercing primitives."""
    cur: Any = obj
    for key in path[:-1]:
        cur = getattr(cur, key)
    final_key = path[-1]
    existing = getattr(cur, final_key, None)
    coerced: Any
    if isinstance(existing, bool):
        coerced = value.lower() in ("1", "true", "yes", "on")
    elif isinstance(existing, int):
        coerced = int(value)
    elif isinstance(existing, float):
        coerced = float(value)
    else:
        coerced = value
    setattr(cur, final_key, coerced)


@cache
def load_yaml_file(name: str) -> dict[str, Any]:
    """Public helper for non-settings YAML (scoring.yaml, entities.yaml, etc.).

    Cached: config files are static for a process lifetime, and extractors call
    this on the hot path (once or twice per page). Treat the result as read-only.
    """
    s = get_settings()
    return _load_yaml(Path(s.config_dir) / name)
