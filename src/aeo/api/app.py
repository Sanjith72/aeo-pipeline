"""
FastAPI application — endpoints from ``PRODUCT_FLOW.md`` §3.

Pure/in-memory endpoints (no DB, deterministic with ``use_llm=false``):
  GET  /api/health            DB + service health
  POST /api/plan              business brief → blueprint + no_website strategy (SP-2)
  POST /api/blueprint         ideal-site blueprint for a topic/domain
  POST /api/deliverables      developer-ready asset bundle, returned inline (SP-3)
Live/DB endpoints:
  POST /api/profile           classify a LIVE site (reuses Orchestrator.dry_run; needs network)
  GET  /api/site-report/{run} persisted site report incl. the SP-1 strategy section (needs DB)

Every handler delegates to an existing ``aeo`` function — no business logic here.

Auth: when ``AEO__API__AUTH_KEY`` is set, all ``/api/*`` routes except ``/api/health``
require a matching ``X-API-Key`` header (see :func:`require_api_key`). Unset = open (dev).
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from ..intelligence.brief import plan_from_brief
from ..reference.business_input import BusinessInput, derive_business_name
from ..reference.competitor_patterns import CompetitorPatterns
from ..reference.framework import Framework
from ..reference.generator import generate_blueprint
from ..report.packager import build_asset_bundle, checklist_for, plan_for
from . import jobs as jobs_mod
from .auth import User, get_current_user, get_optional_user
from .jobs import JOBS

# Bounds for the persisted-plan payloads (B1) — keep a stored plan/profile blob and its
# completed-task set from growing unboundedly through the public plan-state endpoints.
_MAX_JSON_BYTES = 1 * 1024 * 1024
_MAX_DONE_IDS = 5000
_MAX_TASK_ID_LEN = 256
# Bound concurrent deep audits — each spawns a crawl worker thread, so cap the blast radius.
_MAX_CONCURRENT_AUDITS = 4

# Wikidata "About you" enrichment in /api/profile. Originally disabled because the WDQS
# P856 regex scan ran ~38–50s — never inside this endpoint's budget. The resolver now uses
# the sub-budget retrieval path that gate demanded: an exact ``haswbstatement`` P856 search
# + ``wbgetentities`` (measured ~0.4–2s end to end, live-verified on mayoclinic.org), so the
# lookup races the crawl and actually lands. It is ALSO the only industry/offer source for
# sites whose bot walls defeat both the browser-header fetch and the Playwright render
# (mayoclinic.org 403s everything; its Wikidata entity still says "Healthcare"). Local SMBs
# without an entity simply miss → the crawl classifier / user edit carries them, unchanged.
_WIKIDATA_ENRICHMENT_ENABLED = True
# Cap the post-crawl wait on the Wikidata task: it starts concurrently with the crawl
# (~10s), so it has normally finished long before this cap is consulted; the cap only
# bounds the tail case where Wikimedia is slow — enrichment is never worth a hung wizard.
_WIKIDATA_WAIT_BUDGET_SEC = 8.0


def _assert_crawlable_host(domain: str, *, allow_unresolvable: bool = False) -> None:
    """SSRF guard: resolve the target host and reject private/loopback/link-local/
    reserved addresses so an attacker can't point a crawl at internal infrastructure
    (e.g. cloud metadata at 169.254.169.254). Best-effort at the entry point — a
    deeper defense also revalidates each redirect hop in the crawler.

    ``allow_unresolvable`` lets the intake paths (/api/profile, /api/overview) keep
    their never-502 contract: an unresolvable host is no SSRF vector (nothing to
    connect to), and their crawl resolves it to an honest ``route='dead'`` instead of
    a 400 that would dead-end the URL-first flow."""
    raw = domain.strip()
    host = urlparse(raw if "://" in raw else f"//{raw}").hostname or raw
    host = host.split(":")[0].strip()
    if not host:
        raise HTTPException(status_code=400, detail="invalid domain")
    from ..crawl.transport import ip_is_blocked

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        if allow_unresolvable:
            return
        raise HTTPException(status_code=400, detail="domain does not resolve") from None
    for info in infos:
        if ip_is_blocked(info[4][0]):
            raise HTTPException(status_code=400, detail="domain resolves to a non-public address")


def current_api_key() -> str | None:
    """The configured API auth key (AEO__API__AUTH_KEY), or None for open/dev mode."""
    from ..settings import get_settings

    return get_settings().api.auth_key


def require_api_key(request: Request) -> None:
    """Global guard: when an auth key is configured, every ``/api/*`` route except
    ``/api/health`` requires a matching ``X-API-Key`` header. No key configured → open
    (dev). Non-``/api/`` paths (``/docs``, ``/openapi.json``) are never gated."""
    key = current_api_key()
    if not key:
        return
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health":
        return
    # The Developer Handoff read-only VIEW is public by design — the unguessable share
    # token in the path IS the credential, so the GET is never gated by the API key (a
    # developer who got the link has no key). Only the GET: owner-only actions under
    # /api/share/ (e.g. POST /api/share/rotate, which revokes a link) stay authenticated.
    if path.startswith("/api/share/") and request.method == "GET":
        return
    # v5 CH-02b: Stripe posts webhooks from its own infrastructure and cannot send our
    # service key. The endpoint is NOT unauthenticated — it verifies Stripe's HMAC
    # signature over the raw body (payments/stripe.verify_webhook), which is a strictly
    # stronger credential than a shared header for this caller.
    if path == "/api/webhooks/stripe" and request.method == "POST":
        return
    if request.headers.get("x-api-key") != key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """App lifespan. On startup, validate the environment (fail fast on config that can
    never work — covers deployments where uvicorn imports this app directly, bypassing
    ``aeo serve``). On shutdown, close the shared headless-browser pool (used by the
    intake-prefill fallback) so the Chromium subprocess doesn't outlive the server. No-op
    when the pool was never used."""
    from ..startup import validate_settings

    validate_settings(serving=True)
    yield
    from ..crawl.browser_pool import close_pool

    await close_pool()


app = FastAPI(
    title="AEO Pipeline API", version="0.2.0",
    dependencies=[Depends(require_api_key)], lifespan=_lifespan,
)


# ── per-IP rate limiting (in-memory; single-process scale, mirrors the job registry) ──


class _RateLimiter:
    """Fixed-window per-key counter. In-memory — right for the single-process API; a
    multi-worker deployment would need a shared store (Redis). Bounds its own memory by
    dropping expired keys once the map grows large."""

    def __init__(self) -> None:
        self._hits: dict[str, tuple[float, int]] = {}

    def over_limit(self, key: str, limit: int, window: float) -> bool:
        now = time.time()
        start, count = self._hits.get(key, (now, 0))
        if now - start >= window:  # window elapsed → reset
            start, count = now, 0
        count += 1
        self._hits[key] = (start, count)
        if len(self._hits) > 10_000:  # cheap eviction so the map can't grow unbounded
            self._hits = {k: v for k, v in self._hits.items() if now - v[0] < window}
        return count > limit


# Paths exempt from the per-IP throttle. Constants so the webhook literal used here, in
# require_api_key's guard exemption, and in the route decorator can never drift apart.
_HEALTH_PATH = "/api/health"
_STRIPE_WEBHOOK_PATH = "/api/webhooks/stripe"

_RATE = _RateLimiter()
# The free-overview daily caps live in their OWN limiter: the middleware limiter runs a
# 60s window and its overflow eviction drops any entry older than that window, which would
# silently reset a 24h overview counter sharing the same map (a cost-ceiling bypass).
_OVERVIEW_RATE = _RateLimiter()
_DAY_SEC = 86400.0


def _client_ip(request: Request) -> str:
    """The caller's IP — the left-most ``X-Forwarded-For`` entry when behind a proxy
    (Vercel/Railway/the web proxy), else the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """Throttle each client IP on ``/api/*`` (``/api/health`` excluded so liveness probes are
    never limited). No-op when ``AEO__API__RATE_LIMIT`` is 0 (the dev default). Runs before
    auth, so an attacker can't hammer the key check either.

    The Stripe webhook is exempt too: Stripe delivers from a small pool of shared egress IPs
    and retries in bursts, so a 429 there is recorded as a delivery FAILURE — money captured
    with no entitlement written, and after ~3 days Stripe stops retrying. Its HMAC signature
    is a far stronger gate than an IP counter, so it does not need this one."""
    from ..settings import get_settings

    cfg = get_settings().api
    path = request.url.path
    exempt = (_HEALTH_PATH, _STRIPE_WEBHOOK_PATH)
    limited = cfg.rate_limit > 0 and path.startswith("/api/") and path not in exempt
    if limited and _RATE.over_limit(_client_ip(request), cfg.rate_limit, cfg.rate_window_sec):
        return JSONResponse(
            {"detail": "rate limit exceeded — slow down"},
            status_code=429,
            headers={"Retry-After": str(cfg.rate_window_sec)},
        )
    return await call_next(request)


def _install_cors(application: FastAPI) -> None:
    """Allow the web UI (a different origin in every deployment shape) to call the API.
    Origins come from AEO__API__CORS_ORIGINS (comma-separated)."""
    from fastapi.middleware.cors import CORSMiddleware

    from ..settings import get_settings

    origins = [o.strip() for o in get_settings().api.cors_origins.split(",") if o.strip()]
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            # Authorization added for defense-in-depth: browser→proxy is same-origin (no CORS),
            # but this prevents a confusing failure if a browser is pointed straight at the API.
            allow_headers=["Content-Type", "X-API-Key", "Authorization"],
        )


_install_cors(app)


# ── request models ────────────────────────────────────────────────────────────


class BriefRequest(BaseModel):
    # v5 CH-01 (URL-first intake): the URL is the only required input anywhere. A blank
    # name derives from the domain server-side (mirrors the web UI's deriveName), so
    # URL-only callers pass every brief-shaped endpoint; name-only briefs (the
    # no-website path) still work. Only both-blank is rejected.
    name: str = ""
    domain: str | None = None
    category: str | None = None
    topic: str | None = None
    location: str | None = None
    services: list[str] = []
    competitors: list[str] = []
    goals: list[str] = []
    use_llm: bool = True

    @model_validator(mode="after")
    def _default_name_from_domain(self) -> BriefRequest:
        if not self.name.strip():
            derived = derive_business_name(self.domain)
            if not derived:
                raise ValueError("name or domain is required")
            self.name = derived
        return self


class DeliverablesRequest(BriefRequest):
    draft_limit: int = 10
    # Who's building the site — shapes the kit (see report.packager): "dev" keeps the
    # original developer bundle; diy/ai/hire produce the owner-facing packs.
    builder_mode: Literal["dev", "diy", "ai", "hire"] = "dev"


class AgentRunRequest(BriefRequest):
    # Client-supplied dedupe key: a retried POST (double-click, network retry) carrying
    # the same key returns the existing run instead of minting a duplicate.
    idempotency_key: str | None = Field(default=None, max_length=128)


class BlueprintRequest(BaseModel):
    topic: str | None = None
    domain: str | None = None
    category: str | None = None
    use_llm: bool = True


class ProfileRequest(BaseModel):
    domain: str
    max_urls: int | None = None
    use_llm: bool = True


class OverviewRequest(BaseModel):
    """The v5 free-tier entry (CH-09): a URL and nothing else."""

    domain: str
    # Bounded on the anonymous endpoint: a client-supplied max can't be used to force a
    # multi-hundred-MB inventory gather + sort on the event loop.
    max_urls: int | None = Field(default=None, ge=1, le=200)


class AuditRequest(BaseModel):
    domain: str
    name: str | None = None
    # R2-2 re-crawl: bypass the fingerprint skip gate so unchanged pages are re-read.
    force: bool = False


# Whole-request ceiling for /api/competitors/suggest. Per-call timeouts (interactive
# client, 45s) don't bound the product of relaxation-ladder passes (up to 4) × hybrid
# provider chain (up to 4 backends + a healing call each): with hung providers that
# multiplies into many minutes, and every proxy in front of this (Vercel function cap,
# browser fetch) gives up at ~300s — losing the honest `reason` to a generic 504.
_SUGGEST_BUDGET_SEC = 90.0


class CompetitorSuggestRequest(BaseModel):
    name: str = ""  # blank derives from domain (v5 CH-01 URL-only intake)
    domain: str | None = None
    category: str | None = None
    location: str | None = None
    services: list[str] = []  # crawled offerings sharpen the LLM's "direct competitor" sense
    count: int = 6
    verify: bool = False  # live domain HEAD-checks are slow; the picker only needs names


class MilestoneSyncRequest(BaseModel):
    """Persist a generated plan as a client's implementation milestones. ``plan`` is the
    structured plan from ``/api/deliverables`` (report.packager.build_plan output)."""

    domain: str
    name: str | None = None
    plan: dict[str, Any]
    # Detected CMS ('wordpress' | 'shopify' | 'unknown') from /api/profile — persisted on
    # the client so the dashboard's "I'll do it myself" steps match the platform.
    cms_type: str | None = None


class MilestoneTaskUpdate(BaseModel):
    domain: str
    task_key: str
    status: Literal["pending", "in_progress", "verified_completed"]


class MilestoneVerifyRequest(BaseModel):
    domain: str


class ShareRotateRequest(BaseModel):
    domain: str


class EventRequest(BaseModel):
    """One product-analytics event (Block F instrumentation). ``session_id`` is the
    browser-minted, cookie-persisted id that DAU/return-rate are computed over."""

    session_id: str
    event_type: str
    client_id: int | None = None
    url: str | None = None
    metadata: dict[str, Any] = {}


class GrantRequest(BaseModel):
    """Manual/promo entitlement grant (v5 CH-02b; payments stubbed). ``user_id`` is
    supplied explicitly — there is no logged-in user until P4. Admin-only in effect: it
    inherits the global X-API-Key guard. ``user_id``/``expires_at`` are typed so malformed
    values 422 at validation instead of 500-ing on the DB cast (UUID / TIMESTAMPTZ columns)."""

    user_id: UUID
    domain: str
    scope: Literal["free_overview", "pack", "all_packs", "tickets"]
    pack_index: int | None = None
    source: str = "manual"
    expires_at: datetime | None = None


class CheckoutRequest(BaseModel):
    """Buy one pack (v5 CH-02b, flat price per pack). The BUYER is never in the body — it
    comes from the verified JWT, so a caller cannot purchase into another account."""

    domain: str
    pack_index: int


class RedeemRequest(BaseModel):
    """Redeem a promo code to unlock a domain's packs (v5 monetization stub — payments
    deferred; a valid code grants an ``all_packs`` entitlement, source='promo'). The user
    comes from the verified JWT, never the body."""

    domain: str
    code: str


class TicketKeyRequest(BaseModel):
    """A v5 ticket action keyed by its stable task_key (CH-08/CH-15)."""

    task_key: str


class TicketFieldsRequest(BaseModel):
    """Set a v5 ticket's async board fields (CH-08). Omitted fields are left alone;
    ``assignee``/``target_date`` of null clears. ``target_date`` is ISO ``YYYY-MM-DD``."""

    task_key: str
    assignee: str | None = None
    target_date: str | None = None
    set_assignee: bool = False   # explicit flags so null means "clear", omission means "leave"
    set_target_date: bool = False


class OverrideRequest(BaseModel):
    """One human override (R2-4): an edited prefilled value, or a rejected
    recommendation. Captured as eval signal + a human-gated proposal — never auto-applied."""

    session_id: str
    field: str
    old_value: Any | None = None
    new_value: Any | None = None
    kind: str = "field_override"  # or "recommendation_rejected"
    url: str | None = None
    client_id: int | None = None


class PlanStateCreate(BaseModel):
    """Persist the interactive plan so it survives a device switch and earns a resumable
    /plan/<id> link (B1). ``plan`` is the StructuredPlan the UI renders; ``profile`` is a
    SiteProfile snapshot for the score/overview; ``score`` is the canonical AEO score at
    issue time (seeds the score-over-time delta in a later spec)."""

    session_id: str | None = None
    run_id: int | None = None
    business_name: str | None = Field(default=None, max_length=512)
    domain: str | None = Field(default=None, max_length=2048)
    plan: dict[str, Any]
    profile: dict[str, Any] | None = None
    score: int | None = None
    done_task_ids: list[str] = Field(default=[], max_length=_MAX_DONE_IDS)

    @field_validator("plan", "profile")
    @classmethod
    def _bound_json(cls, v: Any) -> Any:
        return _bounded_json(v)

    @field_validator("done_task_ids")
    @classmethod
    def _bound_ids(cls, v: list[str]) -> list[str]:
        return _bounded_ids(v)


class PlanProgressUpdate(BaseModel):
    """Save progress for a plan — the set of completed task ids (and optionally a
    refreshed score). Idempotent; ``score`` is only written when present."""

    done_task_ids: list[str] = Field(default=[], max_length=_MAX_DONE_IDS)
    score: int | None = None

    @field_validator("done_task_ids")
    @classmethod
    def _bound_ids(cls, v: list[str]) -> list[str]:
        return _bounded_ids(v)


def _bounded_json(v: Any) -> Any:
    """Reject a JSONB payload whose serialized form exceeds the per-field cap."""
    if v is not None and len(json.dumps(v, default=str)) > _MAX_JSON_BYTES:
        raise ValueError("payload too large")
    return v


def _bounded_ids(v: list[str]) -> list[str]:
    """Reject an over-long task-id (the list length is already capped by Field)."""
    if any(len(x) > _MAX_TASK_ID_LEN for x in v):
        raise ValueError("task id too long")
    return v


# ── helpers ─────────────────────────────────────────────────────────────────


def _brief(req: BriefRequest) -> BusinessInput:
    return BusinessInput(
        name=req.name, domain=req.domain, category=req.category, topic=req.topic,
        location=req.location, services=req.services, competitors=req.competitors, goals=req.goals,
    )


def _business_dict(brief: BusinessInput) -> dict[str, Any]:
    """The owner-facing facts the packager personalizes the kit with."""
    return {
        "name": brief.name, "category": brief.category,
        "location": brief.location, "services": brief.services,
    }


def _cache_age(domain: str) -> dict[str, Any]:
    """When the domain's homepage was last crawled, as an ISO timestamp + an age in
    hours, so the UI can show "data from N hours ago" and offer a re-crawl (R2-2).
    Best-effort: a down/empty DB returns nulls rather than failing the profile call."""
    from datetime import datetime

    from ..crawl.discovery import seed_url
    from ..storage.repos import pages as pages_repo
    from ..utils.url import normalize

    try:
        last = pages_repo.last_crawled_at(normalize(seed_url(domain)))
    except Exception:  # the profile path must work even with no DB
        last = None
    if last is None:
        return {"last_crawled_at": None, "cache_age_hours": None}
    now = datetime.now(UTC)
    when = last if last.tzinfo else last.replace(tzinfo=UTC)
    age_hours = max(0.0, round((now - when).total_seconds() / 3600.0, 1))
    return {"last_crawled_at": when.isoformat(), "cache_age_hours": age_hours}


def _framework_and_llm(
    brief: BusinessInput, use_llm: bool, *, bounded: bool = False
) -> tuple[Framework, Any]:
    """A brief-tailored framework (curated file if present, else an in-memory bootstrap
    skeleton — LLM-tailored when enabled) + the resolved LLM client. ``bounded=True`` picks
    the short, fail-fast foreground client (:func:`get_interactive_client`) for latency-sensitive
    callers (the synchronous /api/plan call, the polled personalize build), so a slow local
    model degrades in bounded time instead of making the user wait minutes."""
    from ..nlp.llm import get_client, get_interactive_client
    from ..reference.framework_bootstrap import resolve_framework

    llm = (get_interactive_client() if bounded else get_client()) if use_llm else None
    framework = resolve_framework(
        brief.key(), llm=llm, topic=brief.topic_hint(), category=brief.category
    )
    return framework, llm


# ── endpoints ─────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict[str, Any]:
    from ..storage.db import health_check  # lazy: never connect at import

    db_ok = False
    try:
        db_ok = health_check()
    except Exception:  # a down DB must not 500 the health check
        db_ok = False
    return {"status": "ok", "db": "ok" if db_ok else "unreachable"}


@app.post("/api/plan")
def plan(req: BriefRequest) -> dict[str, Any]:
    """Scenario 1: a business brief → ideal-site blueprint + no_website strategy, no crawl.

    Synchronous and user-facing, so it uses the fail-fast foreground client (``bounded=True``):
    a slow/hung local model degrades each LLM call (framework bootstrap, blueprint synthesis,
    profile tiebreak) to deterministic output at ``interactive_timeout_sec`` rather than the
    full per-call ``timeout_sec``."""
    brief = _brief(req)
    framework, llm = _framework_and_llm(brief, req.use_llm, bounded=True)
    return plan_from_brief(brief, framework=framework, llm=llm).to_dict()


@app.post("/api/blueprint")
def blueprint(req: BlueprintRequest) -> dict[str, Any]:
    """Generate the ideal-site blueprint for a topic/domain (deterministic; LLM enriches)."""
    from ..nlp.llm import get_client
    from ..reference.domain_config import normalize_domain
    from ..reference.framework_bootstrap import resolve_framework

    llm = get_client() if req.use_llm else None
    key = (normalize_domain(req.domain) or req.domain) if req.domain else None
    framework = resolve_framework(key, llm=llm, topic=req.topic, category=req.category)
    bp = generate_blueprint(
        topic=req.topic or framework.topic, framework=framework,
        patterns=CompetitorPatterns(), llm=llm,
    )
    return bp.to_jsonb()


def _build_deliverables_payload(req: DeliverablesRequest, progress: Any = None) -> dict[str, Any]:
    """Assemble the full deliverables payload (manifest + interactive plan + strategy +
    checklist + downloadable assets) from a brief.

    The interactive ``plan`` is DETERMINISTIC — ``plan_for`` takes no LLM — so with
    ``use_llm=False`` this returns in seconds (the fast path the in-app "Build my plan"
    uses, identical task content either way). ``use_llm=True`` additionally has the LLM
    write the downloadable page DRAFTS, which is minutes of work on a local model — so that
    path runs as a background job (see ``/api/deliverables/personalize``) rather than
    blocking a request the proxy/keep-alive window would kill. ``progress`` (optional)
    receives ``(stage, counts)`` updates for the job poller."""
    from ..report.strategy import build_strategy
    from ..settings import get_settings

    cfg = get_settings().llm
    brief = _brief(req)
    # The personalized (use_llm) build is the slow background job: bound it with the fail-fast
    # foreground client + a concurrent, wall-clock-budgeted draft phase, so a slow/hung local
    # model degrades to deterministic scaffolds in bounded time rather than running for many
    # minutes. The instant path (use_llm=false) has no LLM, so the draft phase runs sequentially.
    framework, llm = _framework_and_llm(brief, req.use_llm, bounded=True)
    plan_result = plan_from_brief(brief, framework=framework, llm=llm)
    if progress:
        progress("draft", {"pages": req.draft_limit})
    bundle = build_asset_bundle(
        blueprint=plan_result.blueprint, coverage=plan_result.coverage,
        profile=plan_result.profile.to_dict(), origin=brief.domain or brief.key(),
        llm=llm, draft_limit=req.draft_limit,
        builder_mode=req.builder_mode, business=_business_dict(brief),
        draft_workers=cfg.draft_concurrency, draft_budget_sec=cfg.draft_phase_budget_sec,
    )
    # #10 — the prioritized plan as structured JSON: phased, quick-wins flagged, each
    # task carrying current_state/action_required/how_to + AI-vs-human prompts. This
    # is what the interactive in-app checklist renders. Deterministic — same with or
    # without personalization, which is why the in-app plan can always be instant.
    plan = plan_for(
        blueprint=plan_result.blueprint, coverage=plan_result.coverage,
        builder_mode=req.builder_mode, business=_business_dict(brief),
    )
    if progress:
        progress("report", {"assets": len(bundle.assets)})
    return {
        "manifest": bundle.manifest(),
        "plan": plan,
        # R2-5 — the same tasks clustered by difficulty/maturity grade (the Strategy tab),
        # each group with a what/why/how readme + its linked task ids. LLM enriches the
        # readmes when enabled; deterministic otherwise.
        "strategy": build_strategy(plan, llm=llm),
        # Legacy flat-weeks checklist kept for the zip fallback + back-compat.
        "checklist": checklist_for(
            blueprint=plan_result.blueprint, coverage=plan_result.coverage,
            builder_mode=req.builder_mode,
        ),
        "assets": [{"path": a.path, "kind": a.kind, "content": a.content} for a in bundle.assets],
    }


@app.post("/api/deliverables")
def deliverables(req: DeliverablesRequest) -> dict[str, Any]:
    """Build the developer-ready asset bundle from a brief and return it inline (the
    frontend renders / offers each asset for download). The in-app "Build my plan" calls
    this with ``use_llm=false`` for an INSTANT, deterministic plan; the slow LLM-personalized
    build runs as a job (``/api/deliverables/personalize``) so it never blocks the request
    (the cause of the old "Build my plan returns nothing" hang)."""
    return _build_deliverables_payload(req)


@app.post("/api/deliverables.zip")
def deliverables_zip(req: DeliverablesRequest) -> Response:
    """The same developer-ready bundle as ``/api/deliverables``, returned as a single
    downloadable ``.zip`` (one-click 'Download all')."""
    brief = _brief(req)
    framework, llm = _framework_and_llm(brief, req.use_llm)
    plan_result = plan_from_brief(brief, framework=framework, llm=llm)
    bundle = build_asset_bundle(
        blueprint=plan_result.blueprint, coverage=plan_result.coverage,
        profile=plan_result.profile.to_dict(), origin=brief.domain or brief.key(),
        llm=llm, draft_limit=req.draft_limit,
        builder_mode=req.builder_mode, business=_business_dict(brief),
    )
    filename = f"{brief.key()}-aeo-bundle.zip"
    return Response(
        content=bundle.to_zip_bytes(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Bound concurrent personalization builds — each spawns an LLM-heavy worker thread.
_MAX_CONCURRENT_DELIVERABLES = 4


@app.post("/api/deliverables/personalize")
def start_personalize(req: DeliverablesRequest) -> dict[str, Any]:
    """Start the slow, LLM-personalized kit build as a BACKGROUND job; returns a job id to
    poll via ``GET /api/deliverables/{job_id}``. The interactive plan is already instant
    (``POST /api/deliverables`` with ``use_llm=false``) — this upgrades the downloadable
    files to AI-written page drafts without holding a request open for minutes (which the
    proxy / keep-alive window would kill, exactly like the deep audit, which is why that's a
    job too). Deduped per brief; concurrency-capped."""
    req = req.model_copy(update={"use_llm": True})  # personalization always implies the LLM path
    key = (req.domain or req.name or "").strip().lower()
    existing = JOBS.active_for("deliverables", key)
    if existing is not None:
        return {"job_id": existing.id, "status": existing.status}
    if JOBS.active_count("deliverables") >= _MAX_CONCURRENT_DELIVERABLES:
        raise HTTPException(status_code=429, detail="too many builds in progress; please try again shortly")
    job = JOBS.create("deliverables", key=key)
    jobs_mod.spawn_deliverables(
        job.id, build=lambda progress: _build_deliverables_payload(req, progress)
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/api/deliverables/{job_id}")
def personalize_status(job_id: str) -> dict[str, Any]:
    """Poll a personalization job. On ``succeeded``, ``result`` holds the full deliverables
    payload (same shape as ``POST /api/deliverables``)."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return job.to_dict()


@app.post("/api/profile")
async def profile(req: ProfileRequest) -> dict[str, Any]:
    """Classify a LIVE site (reuses the zero-DB dry-run path) and BRANCH ON CRAWL QUALITY.

    The URL-first intake (#1/#2/#3) leans on this: a content-rich/thin site returns its
    SiteProfile with the crawl-derived ``industry`` + ``location`` (so the wizard prefills
    instead of asking), while a dead/unreachable crawl returns ``route='dead'`` pointing at
    the no-website brief path (``/api/plan``) — never a 502, so the flow always continues."""
    import asyncio

    from ..intelligence import DEAD, classify_intake
    from ..intelligence.industry import WikidataProfile, resolve_wikidata_profile
    from ..intelligence.site_facts import SiteFacts, first_clause, gather_site_facts
    from ..pipeline import Orchestrator
    from ..settings import get_settings

    # SSRF guard (private/loopback only): unresolvable hosts continue to route='dead'.
    # getaddrinfo is blocking → off the event loop so a slow/blackholed NS can't freeze
    # every other request on this async handler.
    await asyncio.to_thread(_assert_crawlable_host, req.domain, allow_unresolvable=True)
    cache = _cache_age(req.domain)
    # Crawl the homepage + key pages for Location / what-you-offer / on-site competitors,
    # concurrently with the structural dry-run profile AND the Wikidata enrichment lookup
    # (industry vertical / HQ / products / description via the fast entity-API path — see
    # _WIKIDATA_ENRICHMENT_ENABLED), so the wizard prefills in one round.
    facts_task = asyncio.create_task(gather_site_facts(req.domain))
    wikidata_task = (
        asyncio.create_task(resolve_wikidata_profile(req.domain))
        if _WIKIDATA_ENRICHMENT_ENABLED
        else None
    )
    # The fast intake must return in seconds — the wizard shows a provisional score the
    # instant this lands. So it runs the STRUCTURAL profile deterministically: no sample
    # page drafts and no LLM blueprint/profile synthesis (dozens of slow calls on a local
    # model). LLM personalization belongs to the async deep audit (the bulk client, with
    # live progress) and the deliverables build — never this synchronous request. We accept
    # ``use_llm`` on the request for API compatibility but deliberately don't block on it here.
    result = await Orchestrator().dry_run(
        req.domain, max_urls=req.max_urls, pages=0, use_llm=False, draft_samples=False
    )
    try:
        facts: SiteFacts = await facts_task
    except Exception:  # facts are best-effort enrichment — never fail the profile over them
        facts = SiteFacts()
    wikidata = WikidataProfile()
    if wikidata_task is not None:
        try:
            # Bounded: the task has been running alongside the crawl, so this normally
            # returns immediately; the cap only cuts the slow-Wikimedia tail loose.
            wikidata = await asyncio.wait_for(wikidata_task, timeout=_WIKIDATA_WAIT_BUDGET_SEC)
        except Exception:  # timeout/network — best-effort enrichment, never fail the profile
            wikidata = WikidataProfile()
    intake = get_settings().intake
    discovered = int(result.get("discovered") or 0)
    # The live profile path doesn't fetch body text, so the gate is page-count based.
    route = classify_intake(
        discovered, None,
        min_pages=intake.thin_site_min_pages, min_words=intake.thin_site_min_words,
    )
    # Specific industry, best source first: Wikidata vertical → crawl-classified vertical
    # → the structural profile's coarse label (business-model fallback). This is what
    # keeps generic "Enterprise" from surfacing whenever a real vertical is knowable.
    prof = result.get("profile")
    industry = wikidata.industry or facts.industry or (prof.get("industry") if prof else None)
    industry_source = (
        "wikidata" if wikidata.industry else "crawl" if facts.industry else "model" if industry else None
    )
    # Wikidata HQ as a location string ("London, United Kingdom" / just the city / just the
    # country) — the enrichment fallback when the crawl found no address on the page.
    wikidata_location = (
        f"{wikidata.location}, {wikidata.country}"
        if wikidata.location and wikidata.country
        else wikidata.location or wikidata.country
    )
    # Location, best source first: crawl address/schema (most precise) → Wikidata HQ →
    # the structural profile's URL-path guess.
    location = facts.location or wikidata_location or (prof.get("location") if prof else None)
    # What you offer, best source first: crawl offerings (schema.org / service pages,
    # with the crawl's own description-clause fallback already applied inside
    # extract_facts) → Wikidata products produced (P1056) → the first clause of the
    # Wikidata one-liner → the industry label. The two tail rungs are what keep this
    # field populated for sites whose bot walls blank the whole crawl (mayoclinic.org):
    # a short honest phrase beats an empty box the user must fill from scratch.
    services = facts.services or wikidata.offerings
    if not services and wikidata.description:
        clause = first_clause(wikidata.description)
        services = [clause] if clause else []
    if not services and industry:
        services = [industry]
    # A one-line "about" blurb — a crawl-derived summary would win if we had one, so today
    # this surfaces the Wikidata schema:description when present.
    about = wikidata.description
    if prof is None or route == DEAD:
        # Crawl found nothing usable → the no-website brief path is the right flow.
        return {
            "route": DEAD,
            "profile": None,
            "industry": industry,
            "industry_source": industry_source,
            "location": location,
            "services": services,
            "about": about,
            "competitors": facts.competitors,
            "cms_type": facts.cms_type,
            "discovered": discovered,
            "source": result.get("source"),
            "next": "/api/plan",
            **cache,
        }
    return {
        "route": route,  # 'rich' | 'thin'
        "profile": prof,
        "industry": industry,
        "industry_source": industry_source,
        "location": location,
        "services": services,
        "about": about,
        "competitors": facts.competitors,
        "cms_type": facts.cms_type,
        "coverage": result["coverage"],
        "discovered": discovered,
        "source": result["source"],
        **cache,
    }


@app.post("/api/overview")
async def overview(req: OverviewRequest, request: Request) -> dict[str, Any]:
    """The v5 free overview (CH-09/CH-16 slice 1): paste a URL, get the structural
    profile + five homepage skill scores + an impact-ordered pack preview + on-site
    competitor names — no signup, no persisted run. Composition lives in
    ``pipeline.overview.build_overview``; this handler only adds the free-tier
    protections (§9.4): per-domain daily cache (hits are free and uncounted), the SSRF
    guard, and a per-IP daily cap on fresh builds (AEO__API__OVERVIEW_DAILY_LIMIT;
    0 = off for dev)."""
    from ..pipeline.overview import build_overview, cached_overview
    from ..settings import get_settings

    domain = req.domain.strip()
    if not domain:
        raise HTTPException(status_code=422, detail="domain is required")
    cached = cached_overview(domain)
    if cached is not None:
        return cached  # cache hits are free — never counted against either cap
    # SSRF guard (private/loopback only), off the event loop; unresolvable → route='dead'.
    await asyncio.to_thread(_assert_crawlable_host, domain, allow_unresolvable=True)
    cfg = get_settings().api
    # Per-IP daily cap (best-effort; spoofable via X-Forwarded-For) AND a global daily
    # ceiling that no single-IP trick can bypass — the infra-independent backstop for the
    # §9.4 free-tier cost ceiling. Both bound FRESH (crawling) builds only.
    if cfg.overview_daily_limit > 0 and _OVERVIEW_RATE.over_limit(
        f"overview:{_client_ip(request)}", cfg.overview_daily_limit, _DAY_SEC
    ):
        raise HTTPException(
            status_code=429,
            detail="free analysis limit reached for today — come back tomorrow",
            headers={"Retry-After": "86400"},
        )
    if cfg.overview_global_daily_limit > 0 and _OVERVIEW_RATE.over_limit(
        "overview:__global__", cfg.overview_global_daily_limit, _DAY_SEC
    ):
        raise HTTPException(
            status_code=429,
            detail="free analysis is busy right now — please try again later",
            headers={"Retry-After": "3600"},
        )
    return await build_overview(domain, max_urls=req.max_urls)


@app.post("/api/competitors/suggest")
async def competitors_suggest(req: CompetitorSuggestRequest) -> dict[str, Any]:
    """Likely competitors for a business brief (name + category + location), so the UI
    can offer a pick-list instead of demanding URLs. Source is ``llm`` when the LLM
    generated suggestions; ``onsite`` when we mined the site's own comparison/alternatives
    pages instead (LLM disabled, unreachable, or it proposed nothing usable);
    ``unavailable`` when neither yielded anything. Unavailable responses carry a
    ``reason`` so the UI can be honest about WHY: ``llm_disabled`` (this deployment has
    no AI configured — permanent until ops sets keys, don't imply the business is at
    fault), ``llm_failed`` (providers errored/timed out — transient, retry-worthy), or
    ``no_results`` (the AI ran fine and genuinely proposed nothing usable)."""
    import asyncio

    from ..intelligence.site_facts import gather_site_facts
    from ..nlp.llm import get_interactive_client
    from ..reference.competitor_discovery import discover_competitors

    # URL-only intake (v5 CH-01): a blank name derives from the domain, like BriefRequest.
    name = req.name.strip() or derive_business_name(req.domain)
    if not name:
        raise HTTPException(status_code=422, detail="name or domain is required")
    domain = (req.domain or "").strip()

    async def onsite() -> dict[str, Any] | None:
        # Best-effort on-site signals (comparison / alternatives pages) — the fallback
        # both when the LLM is off AND when it's configured but broken (daemon down,
        # model not pulled): an enabled-but-failing LLM must degrade the same way, not
        # hand the UI a blank picker labelled "llm".
        if not domain:
            return None
        facts = await gather_site_facts(domain)
        if facts.competitors:
            return {"competitors": facts.competitors[: req.count], "source": "onsite"}
        return None

    # The INTERACTIVE client: same provider chain as the shared client, but with the
    # short fail-fast generation timeout and a single attempt per backend (hybrid
    # failover still walks the chain). A user is parked on the wizard's competitor step
    # while this runs — a slow or hung provider must degrade in seconds, not wait out
    # the bulk 120s timeout × retry backoff across four relaxation-ladder passes.
    # _SUGGEST_BUDGET_SEC then bounds the WHOLE walk: per-call timeouts don't cap the
    # ladder (4 passes) × provider chain (up to 4 backends + healing) product, and the
    # Vercel proxy in front of this dies at 300s — better to answer llm_failed in 90s
    # than have the proxy 504 and collapse the honest reason into a generic error.
    llm = get_interactive_client()
    if not llm.enabled:
        return (await onsite()) or {
            "competitors": [], "source": "unavailable", "reason": "llm_disabled"
        }
    # discover_competitors is synchronous (blocking LLM calls + HEAD probes, up to four
    # relaxation-ladder passes) — run it in a worker thread so a slow local model can't
    # freeze every other request on the event loop for minutes. On budget expiry the
    # orphaned thread finishes harmlessly (it mutates nothing shared) while we answer.
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                discover_competitors,
                name,
                domain,
                topic=(req.category or "").strip() or None,
                location=(req.location or "").strip() or None,
                services=req.services,
                count=req.count,
                llm=llm,
                head_check=None if req.verify else (lambda _domain: True),
            ),
            timeout=_SUGGEST_BUDGET_SEC,
        )
    except TimeoutError:
        from ..logging import get_logger

        get_logger(__name__).warning(
            "competitor_suggest_budget_exhausted", name=name, domain=domain
        )
        return (await onsite()) or {
            "competitors": [], "source": "unavailable", "reason": "llm_failed"
        }
    if not result.verified:
        # llm_ok distinguishes "the AI answered and found nothing" (no_results — the
        # honest blank) from "the AI never answered usably" (llm_failed — transient,
        # the UI should offer a retry instead of implying the business has no peers).
        # A third case only verify=true callers can hit: the AI proposed candidates but
        # live domain probes dropped them all — that's our network/their WAF, so it gets
        # its own label instead of the false "no peers exist" (verify=false callers
        # can't reach it: with probes skipped, proposed ⇒ verified).
        if not result.llm_ok:
            reason = "llm_failed"
        elif result.raw_count > 0:
            reason = "verification_failed"
        else:
            reason = "no_results"
        return (await onsite()) or {"competitors": [], "source": "unavailable", "reason": reason}
    return {
        "competitors": [{"name": c.name, "domain": c.domain} for c in result.verified],
        "source": "llm",
        # True when the strict industry+location pass was empty and a broadened pass
        # supplied these — lets the UI hint "broader matches" instead of implying exactness.
        "relaxed": result.relaxed,
    }


@app.get("/api/site-report/{run_id}")
def site_report(run_id: int) -> dict[str, Any]:
    """The persisted site report for a run, including the SP-1 ``strategy`` section."""
    from ..storage.repos import site_reports as site_reports_repo

    row = site_reports_repo.for_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"no site report for run {run_id}")
    return dict(row)


def _grants_for(user: User | None, run_id: int) -> list[dict[str, Any]]:
    """The viewer's currently-valid entitlement rows for the run's domain — the input to
    the lock resolver. Anonymous (no user) or a run with no resolvable domain → ``[]`` (the
    P3 anonymous path: Pack 1 unlocked, deeper locked). ``user_id`` comes ONLY from the
    verified JWT, never the request, so no one can unlock another user's domain."""
    if user is None:
        return []
    from ..storage.repos import entitlements as entitlements_repo
    from ..storage.repos import runs as runs_repo

    domain = runs_repo.domain_for_run(run_id)
    return entitlements_repo.list_for_user_domain(user.id, domain) if domain else []


def _pack_locked_for(user: User | None, run_id: int) -> Callable[[int], bool]:
    """A ``locked(pack_index)`` predicate for this viewer on this run — the SAME derivation
    the pack routes use (``_grants_for`` → ``resolve_unlock_state`` → ``is_pack_locked``), so
    the ticket gate and the pack gate can never drift apart. Resolved once per request (one
    grants query + one completion query) rather than per ticket. Deliberately independent of
    the persisted pack headers: a run whose ``packs`` rows are missing still gates by the
    same rule (Pack 1 free, deeper needs a grant or the earned-forward completion)."""
    from ..entitlements.logic import is_pack_locked, resolve_unlock_state
    from ..storage.repos import packs as packs_repo

    all_packs, unlocked = resolve_unlock_state(_grants_for(user, run_id))
    completed = packs_repo.completed_pack_indices(run_id)

    def locked(pack_index: int) -> bool:
        return is_pack_locked(
            int(pack_index), unlocked_pack_indices=unlocked,
            all_packs=all_packs, completed_pack_indices=completed,
        )

    return locked


def _require_unlocked_pack(user: User | None, run_id: int, pack_index: int | None) -> None:
    """403 unless this viewer has the pack unlocked. Used by every ticket route: a ticket
    carries the same page×skill deep value the gated pack detail does, so leaving the
    ticket routes open would make the pack-detail 403 bypassable (v5 CH-02a)."""
    if pack_index is None or _pack_locked_for(user, run_id)(pack_index):
        raise HTTPException(status_code=403, detail="unlock this pack to work its tickets")


def _require_ticket_owner(user: User | None, run_id: int, client_id: int) -> None:
    """403 unless this viewer may MUTATE this client's v5 tickets (P5 per-user ownership).

    Migration 0031 stamped ``owner_user_id`` but nothing enforced it, so any logged-in user
    with an entitlement on a domain could close another user's tickets — and closing drives
    progressive unlock and spends crawl budget.

    Three ways through, in order:
      * **Unowned** — generated anonymously (the signed-out free Pack-1 flow). Left open, or
        enabling this would break the anonymous experience the free tier depends on.
      * **The owner** — the user the board was stamped for.
      * **An ``all_packs`` holder** — the explicit agency/advanced override from §9.2's
        entitlement model. Blocking them here would defeat the override's whole purpose.
    """
    from ..storage.repos import entitlements as entitlements_repo
    from ..storage.repos import milestones as milestones_repo
    from ..storage.repos import runs as runs_repo

    owner = milestones_repo.pack_owner_of(client_id)
    if owner is None:
        return
    if user is not None:
        if user.id == owner:
            return
        domain = runs_repo.domain_for_run(run_id)
        grants = entitlements_repo.list_for_user_domain(user.id, domain) if domain else []
        if any(g.get("scope") == "all_packs" for g in grants):
            return
    raise HTTPException(status_code=403, detail="these tickets belong to another account")


@app.get("/api/packs/{run_id}")
def get_packs(run_id: int, user: User | None = Depends(get_optional_user)) -> dict[str, Any]:
    """The impact-ordered packs persisted for a run (v5 CH-03), each with its
    entitlement-derived ``locked`` flag. Empty ``packs`` (200, not 404) for older or
    dry-run-only runs that never persisted packs — the UI falls back to the live overview
    preview. Auth-aware (v5 CH-02a): anonymous callers get Pack 1 unlocked + deeper locked;
    a logged-in user gets their REAL grants. Login alone never unlocks — only an entitlement
    row does (``completed_pack_indices`` is empty until P5)."""
    from ..entitlements.logic import decorate_pack
    from ..storage.repos import packs as packs_repo

    rows = packs_repo.by_run(run_id)
    grants = _grants_for(user, run_id)
    completed = packs_repo.completed_pack_indices(run_id)  # v5 CH-15: earned-forward unlock
    return {"run_id": run_id, "packs": [decorate_pack(r, grants=grants, completed=completed) for r in rows]}


@app.get("/api/packs/{run_id}/{pack_index}")
def get_pack_detail(
    run_id: int, pack_index: int, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """The gated deep value (v5 CH-02a): a pack's per-page five-skill detail (scores +
    suggestions + priorities). Enforced SERVER-SIDE — a locked pack returns 403 regardless
    of what the client renders (we never ship locked detail with a flag the browser can
    ignore). Pack 1 is unlocked for everyone (overview stays public); deeper packs require
    both a logged-in user AND a real entitlement (anonymous → no grants → locked → 403)."""
    from ..entitlements.logic import decorate_pack
    from ..storage.repos import packs as packs_repo
    from ..storage.repos import skill_scores as skill_scores_repo

    header = next((r for r in packs_repo.by_run(run_id) if r["pack_index"] == pack_index), None)
    if header is None:
        raise HTTPException(status_code=404, detail="no such pack")
    completed = packs_repo.completed_pack_indices(run_id)
    if decorate_pack(header, grants=_grants_for(user, run_id), completed=completed)["locked"]:
        raise HTTPException(status_code=403, detail="unlock this pack to view its detail")
    return {
        "run_id": run_id, "pack_index": pack_index, "title": header["title"],
        "pages": skill_scores_repo.detail_for_pack(run_id, pack_index),
    }


# ── v5 tickets (CH-08 board + CH-15 before/after verify) ────────────────────────


def _ticket_client(run_id: int) -> tuple[int, Any]:
    """(client_id, target) for a run's domain, or 404 when the run has no resolvable
    domain / persisted work. Tickets are domain-keyed, reusing the milestone chain."""
    from ..storage.repos import runs as runs_repo
    from ..storage.repos import targets as targets_repo

    domain = runs_repo.domain_for_run(run_id)
    target = targets_repo.by_domain(domain) if domain else None
    if target is None:
        raise HTTPException(status_code=404, detail="no tickets for this run yet")
    return target.id, target


@app.get("/api/tickets/{run_id}")
def get_tickets(run_id: int, user: User | None = Depends(get_optional_user)) -> dict[str, Any]:
    """The v5 tickets for a run's packs (CH-08): one per (page, skill), with status /
    assignee / target_date / baseline→current score. Lazily GENERATES them on first view
    (stamping owner_user_id when a logged-in user views) so tickets exist even for audits
    that predate this path. Empty list (200) when the run produced no packs.

    **Gated (v5 CH-02a):** the response is FILTERED to the viewer's unlocked packs. A ticket
    carries the same page×skill deep value as the gated pack detail, so returning every
    pack's tickets here would make the pack-detail 403 pointless. Pack 1 stays free (the
    anonymous tier is unchanged); deeper packs need a grant or the earned-forward unlock."""
    from ..storage.repos import milestones as milestones_repo
    from ..storage.repos import runs as runs_repo
    from ..storage.repos import targets as targets_repo

    domain = runs_repo.domain_for_run(run_id)
    if not domain:
        return {"run_id": run_id, "tickets": []}
    target = targets_repo.by_domain(domain)
    tickets = milestones_repo.list_tickets_for_run(target.id) if target else []
    if not tickets:
        milestones_repo.generate_tickets_from_run(run_id, owner_user_id=(user.id if user else None))
        target = targets_repo.by_domain(domain)
        tickets = milestones_repo.list_tickets_for_run(target.id) if target else []
    locked = _pack_locked_for(user, run_id)
    visible = [t for t in tickets if not locked(t.get("pack_index"))]
    return {
        "run_id": run_id,
        "tickets": visible,
        # So the board can say "3 more fixes in locked packs" without leaking them.
        "locked_ticket_count": len(tickets) - len(visible),
    }


@app.get("/api/tickets/{run_id}/{pack_index}")
def get_pack_tickets(
    run_id: int, pack_index: int, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """The v5 tickets for one pack of a run. Gated exactly like the pack detail (CH-02a):
    a locked pack is a 403, never a filtered-empty 200."""
    from ..storage.repos import milestones as milestones_repo

    client_id, _ = _ticket_client(run_id)
    _require_unlocked_pack(user, run_id, pack_index)
    return {"run_id": run_id, "pack_index": pack_index,
            "tickets": milestones_repo.list_tickets_for_run(client_id, pack_index)}


@app.post("/api/tickets/{run_id}/fields")
def set_ticket_fields(
    run_id: int, req: TicketFieldsRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Set a ticket's assignee / target_date (CH-08 async board). Gated: you can only edit a
    ticket in a pack you have unlocked."""
    from ..storage.repos import milestones as milestones_repo

    client_id, _ = _ticket_client(run_id)
    existing = milestones_repo.get_ticket(client_id, req.task_key)
    if existing is None:
        raise HTTPException(status_code=404, detail="no such ticket")
    _require_unlocked_pack(user, run_id, existing.get("pack_index"))
    _require_ticket_owner(user, run_id, client_id)
    kwargs: dict[str, Any] = {}
    if req.set_assignee:
        kwargs["assignee"] = (req.assignee or None)
    if req.set_target_date:
        kwargs["target_date"] = (req.target_date or None)
    ticket = milestones_repo.set_ticket_fields(client_id, req.task_key, **kwargs)
    if ticket is None:
        raise HTTPException(status_code=404, detail="no such ticket")
    return {"ticket": ticket}


@app.post("/api/tickets/{run_id}/close")
def close_ticket(
    run_id: int, req: TicketKeyRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Owner marks a ticket done (CH-15): → closed_pending_verify, then enqueue a
    FORCED re-crawl of its page so the re-score can prove the lift (an unchanged page
    would otherwise fingerprint-skip and never verify). The frontend polls the ticket
    until it flips to verified_completed.

    Gated (CH-02a): closing costs a real crawl AND drives progressive unlock, so it is
    restricted to packs the viewer has unlocked — otherwise anyone could burn crawl budget
    on any run and earn their way into paid packs for free."""
    from ..pipeline import worker
    from ..storage.repos import milestones as milestones_repo

    client_id, target = _ticket_client(run_id)
    existing = milestones_repo.get_ticket(client_id, req.task_key)
    if existing is None:
        raise HTTPException(status_code=404, detail="no such open ticket")
    _require_unlocked_pack(user, run_id, existing.get("pack_index"))
    _require_ticket_owner(user, run_id, client_id)
    ticket = milestones_repo.close_ticket(client_id, req.task_key)
    if ticket is None:
        raise HTTPException(status_code=404, detail="no such open ticket")
    page_url = ticket.get("page_url")
    job_id = None
    if page_url:
        try:
            job_id = worker.enqueue_batch(
                [page_url], target.name, label=f"verify:ticket:{req.task_key}", force_recrawl=True
            )
        except Exception as exc:  # verification is best-effort; the recheck button retries
            from ..logging import get_logger

            get_logger(__name__).warning("ticket_recrawl_enqueue_failed", task_key=req.task_key, error=str(exc))
    return {"ticket": ticket, "verify_job_id": job_id}


@app.post("/api/tickets/{run_id}/reopen")
def reopen_ticket(
    run_id: int, req: TicketKeyRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Reopen a closed-pending-verify ticket (CH-08). Gated like close."""
    from ..storage.repos import milestones as milestones_repo

    client_id, _ = _ticket_client(run_id)
    existing = milestones_repo.get_ticket(client_id, req.task_key)
    if existing is None:
        raise HTTPException(status_code=404, detail="no such ticket to reopen")
    _require_unlocked_pack(user, run_id, existing.get("pack_index"))
    _require_ticket_owner(user, run_id, client_id)
    ticket = milestones_repo.reopen_ticket(client_id, req.task_key)
    if ticket is None:
        raise HTTPException(status_code=404, detail="no such ticket to reopen")
    return {"ticket": ticket}


@app.post("/api/tickets/{run_id}/recheck")
def recheck_ticket(
    run_id: int, req: TicketKeyRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Re-run verification on a ticket the owner already closed (CH-15): re-enqueue the
    FORCED re-crawl of its page without changing the ticket's status. Used by the "Recheck"
    affordance when a first re-crawl didn't yet prove the lift (edit not live / regressed).
    Gated like close — it spends the same crawl budget."""
    from ..pipeline import worker
    from ..storage.repos import milestones as milestones_repo

    client_id, target = _ticket_client(run_id)
    ticket = milestones_repo.get_ticket(client_id, req.task_key)
    if ticket is None:
        raise HTTPException(status_code=409, detail="ticket is not awaiting verification")
    # Authorize BEFORE validating state: a 409-vs-403 split would otherwise let a caller
    # probe the status of tickets in packs they cannot see.
    _require_unlocked_pack(user, run_id, ticket.get("pack_index"))
    _require_ticket_owner(user, run_id, client_id)
    if ticket["status"] != "closed_pending_verify":
        raise HTTPException(status_code=409, detail="ticket is not awaiting verification")
    job_id = None
    if ticket.get("page_url"):
        try:
            job_id = worker.enqueue_batch(
                [ticket["page_url"]], target.name,
                label=f"verify:ticket:{req.task_key}", force_recrawl=True,
            )
        except Exception as exc:
            from ..logging import get_logger

            get_logger(__name__).warning("ticket_recheck_enqueue_failed", task_key=req.task_key, error=str(exc))
    return {"ticket": ticket, "verify_job_id": job_id}


@app.post("/api/audit")
def start_audit(req: AuditRequest) -> dict[str, Any]:
    """Start a deep audit (full crawl → score → analyze → site report) on a dedicated
    worker thread, so it never blocks the API event loop. Returns a job id to poll via
    ``GET /api/audit/{job_id}``. Needs a live DB + network."""
    domain = req.domain.strip()
    if not domain:
        raise HTTPException(status_code=422, detail="domain is required")
    _assert_crawlable_host(domain)  # SSRF guard — reject internal/loopback targets
    # Dedupe: an audit already in flight for this domain → return it rather than spawn
    # another (collapses double-clicks and overlapping wizard + re-check requests).
    existing = JOBS.active_for("audit", domain)
    if existing is not None:
        return {"job_id": existing.id, "status": existing.status}
    # Concurrency cap: each audit spawns a crawl worker thread, so bound the blast radius.
    if JOBS.active_count("audit") >= _MAX_CONCURRENT_AUDITS:
        raise HTTPException(status_code=429, detail="too many audits in progress; please try again shortly")
    job = JOBS.create("audit", key=domain)
    jobs_mod.spawn_audit(
        job.id, domain=domain, name=(req.name or domain).strip(), force_recrawl=req.force
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/api/audit/{job_id}")
def audit_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return job.to_dict()


@app.post("/api/audit/{job_id}/cancel")
def audit_cancel(job_id: str) -> dict[str, Any]:
    """Cooperatively cancel a running audit (R2-2 drop-off safety). The audit polls the
    flag between pages and early-exits, so pages already analyzed are kept. Idempotent."""
    job = JOBS.request_cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return job.to_dict()


# ── agent runs (Phase 2A: assistive copilot + human approval gate) ──────────────


def _decide_agent_run(run_id: str, decision: str) -> dict[str, Any]:
    """Approve/reject gate: only a 'staged' run can be decided, and only a human does it.
    The write is a compare-and-set from 'staged', so of two racing decisions (or a decision
    racing a cancel) exactly one wins — the loser gets the 409, never a false 200."""
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    if not agent_runs_repo.set_status(run_id, decision, only_from=("staged",)):
        now = agent_runs_repo.get(run_id)
        raise HTTPException(status_code=409, detail=f"run is {(now or row)['status']}, not staged")
    return {"run_id": run_id, "status": decision}


@app.post("/api/agent/run")
def agent_run_start(req: AgentRunRequest) -> dict[str, Any]:
    """Start an assistive agent run. The Planner stages a task graph for human review; nothing
    is published. Returns the run id to poll."""
    if req.domain:
        _assert_crawlable_host(req.domain)  # SSRF parity — the run may crawl this domain later
    from ..agents.runtime import start_agent_run
    from ..settings import get_settings
    from ..storage.repos import agent_runs as agent_runs_repo

    # A replayed idempotency key answers BEFORE the cap: the run already exists (it may
    # itself be what fills the queue), so the retry must learn its id, not get a 429.
    if req.idempotency_key:
        existing = agent_runs_repo.by_idempotency_key(req.idempotency_key)
        if existing is not None:
            return {"run_id": existing["id"], "status": existing["status"]}
    # Backpressure, mirroring the audit cap: each run is a multi-minute LLM+crawl job, so
    # bound how many may be in flight (queued/planning) at once. Soft cap — a race between
    # two POSTs can overshoot by one, which is fine for what it protects (quota + queue depth).
    cap = max(1, get_settings().agents.concurrency)
    if agent_runs_repo.count_active() >= cap:
        raise HTTPException(
            status_code=429,
            detail=f"{cap} agent runs are already in flight — wait for the queue to drain",
        )
    row = start_agent_run(_brief(req).to_dict(), idempotency_key=req.idempotency_key)
    return {"run_id": row["id"], "status": row["status"]}


@app.get("/api/agent/run/{run_id}")
def agent_run_status(run_id: str) -> dict[str, Any]:
    """The run's status + its per-step trace (the staged task graph is in ``result``)."""
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    return {**row, "steps": agent_runs_repo.steps_for(run_id)}


@app.post("/api/agent/run/{run_id}/approve")
def agent_run_approve(run_id: str) -> dict[str, Any]:
    return _decide_agent_run(run_id, "approved")


@app.post("/api/agent/run/{run_id}/reject")
def agent_run_reject(run_id: str) -> dict[str, Any]:
    return _decide_agent_run(run_id, "rejected")


_AGENT_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})
_AGENT_STATUSES = frozenset({"queued", "planning"}) | _AGENT_TERMINAL


@app.post("/api/agent/run/{run_id}/cancel")
def agent_run_cancel(run_id: str) -> dict[str, Any]:
    """Cancel a queued/in-flight run. Compare-and-set from queued/planning — a run that
    staged (or settled) in the meantime 409s with its real status instead of being yanked
    out from under the reviewer. On success the not-yet-claimed job is killed so no worker
    picks it up; a worker already mid-run has its finishing writes discarded by the repo's
    settled-status guard."""
    from ..pipeline.worker import AGENT_RUN
    from ..storage.repos import agent_runs as agent_runs_repo
    from ..storage.repos import jobs as jobs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    if not agent_runs_repo.set_status(run_id, "cancelled", only_from=("queued", "planning")):
        now = agent_runs_repo.get(run_id)
        raise HTTPException(status_code=409, detail=f"run is already {(now or row)['status']}")
    jobs_repo.cancel_pending(AGENT_RUN, run_id)
    return {"run_id": run_id, "status": "cancelled"}


@app.get("/api/agent/run/{run_id}/assets")
def agent_run_assets(run_id: str) -> dict[str, Any]:
    """The run's drafts as launch-kit assets (README + pages/<slug>.md, same shape as
    /api/deliverables). Approval is the gate: anything not 'approved' 409s — drafts
    leave the review queue only after a human signs off."""
    from ..agents.export import bundle_from_run
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    if row["status"] != "approved":
        raise HTTPException(
            status_code=409, detail=f"run is {row['status']} — only approved runs export"
        )
    bundle = bundle_from_run(row)
    return {
        "run_id": run_id,
        "bundle": bundle.name,
        "assets": [{"path": a.path, "kind": a.kind, "content": a.content} for a in bundle.assets],
    }


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@app.get("/api/agent/runs")
def agent_runs_list(status: str = "staged", limit: int = 50) -> dict[str, Any]:
    """Agent runs by status (default 'staged' — the review queue). ``status`` accepts a
    comma-separated list, e.g. ``?status=queued,planning`` for the in-flight view."""
    from ..storage.repos import agent_runs as agent_runs_repo

    statuses = [s.strip() for s in status.split(",") if s.strip()]
    unknown = [s for s in statuses if s not in _AGENT_STATUSES]
    if not statuses or unknown:
        raise HTTPException(status_code=400, detail=f"unknown status: {', '.join(unknown) or '(empty)'}")
    return {"runs": agent_runs_repo.list_by_status(statuses, limit=max(1, min(limit, 200)))}


@app.get("/api/agent/run/{run_id}/stream")
async def agent_run_stream(run_id: str) -> StreamingResponse:
    """Stream a run's steps + status as Server-Sent Events. Polls the durable agent tables
    (so it works across the API↔worker process boundary) and closes once the run is terminal."""
    from ..storage.repos import agent_runs as agent_runs_repo

    async def gen():
        seen = 0
        for _ in range(600):  # ~10 min ceiling at 1s/poll
            row = await asyncio.to_thread(agent_runs_repo.get, run_id)
            if row is None:
                yield _sse({"type": "error", "detail": "unknown agent run"})
                return
            steps = await asyncio.to_thread(agent_runs_repo.steps_for, run_id)
            for step in steps[seen:]:
                yield _sse({"type": "step", "step": step})
            seen = len(steps)
            yield _sse({"type": "status", "status": row["status"], "current_step": row.get("current_step")})
            if row["status"] in _AGENT_TERMINAL:
                yield _sse({"type": "done", "status": row["status"], "result": row.get("result")})
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── gamification (Phase 3: honest, verified-outcome rewards) ─────────────────────


class GamifyReconcileRequest(BaseModel):
    session_id: str
    domain: str | None = None
    aeo_score: int | None = None


@app.get("/api/gamification")
def gamification_get(session_id: str, domain: str | None = None) -> dict[str, Any]:
    """The companion state + recent awards for a session. Best-effort: empty on any miss."""
    from ..storage.repos import gamification as gamification_repo

    try:
        state = gamification_repo.get_state(session_id)
        awards = gamification_repo.awards_for(session_id) if state else []
    except Exception:  # gamification must never break the app
        return {"state": None, "awards": []}
    return {"state": state, "awards": awards}


@app.post("/api/gamification/reconcile")
def gamification_reconcile(req: GamifyReconcileRequest) -> dict[str, Any]:
    """Recompute verified-win awards + score tiers for a session/domain. Idempotent +
    best-effort (a failure resolves to a no-op so the UI never breaks)."""
    from ..companion import rewards

    try:
        return rewards.reconcile(req.session_id, req.domain, aeo_score=req.aeo_score)
    except Exception:
        return {"new_awards": [], "unlocked": [], "state": None}


@app.post("/api/events")
def record_event(req: EventRequest) -> dict[str, Any]:
    """Record one product-analytics event (Block F). Best-effort by design: analytics
    must never break the user's flow, so a DB hiccup returns ``{"recorded": false}``
    rather than 500ing. The frontend fires these fire-and-forget (see ``track``)."""
    from ..logging import get_logger
    from ..storage.repos import events as events_repo

    sid = req.session_id.strip()
    etype = req.event_type.strip()
    if not sid or not etype:
        raise HTTPException(status_code=422, detail="session_id and event_type are required")
    try:
        event_id = events_repo.record(
            session_id=sid, event_type=etype,
            client_id=req.client_id, url=req.url, metadata=req.metadata,
        )
    except Exception as exc:  # never break the flow over analytics
        get_logger(__name__).warning("event_record_failed", event_type=etype, error=str(exc))
        return {"recorded": False}
    return {"recorded": True, "id": event_id}


@app.post("/api/overrides")
def record_override(req: OverrideRequest) -> dict[str, Any]:
    """Capture a human override (R2-4) as BOTH an analytics event and a PROPOSED criteria
    refinement in the human-gated queue. The learning loop is capture → propose →
    human-validate: this handler only ever creates a ``status='proposed'`` refinement and
    NEVER accepts/auto-applies one (the v4 circular-validation guard). Best-effort, like
    ``/api/events`` — a DB hiccup degrades gracefully rather than breaking the flow."""
    from ..logging import get_logger
    from ..reference.feedback import propose_refinement_from_override
    from ..storage.repos import events as events_repo
    from ..storage.repos import feedback as feedback_repo

    log = get_logger(__name__)
    field = req.field.strip()
    sid = req.session_id.strip()
    if not field or not sid:
        raise HTTPException(status_code=422, detail="session_id and field are required")

    event_recorded = False
    refinement_id: int | None = None
    try:
        events_repo.record(
            session_id=sid, event_type=f"override:{req.kind}",
            client_id=req.client_id, url=req.url,
            metadata={"field": field, "old": req.old_value, "new": req.new_value},
        )
        event_recorded = True
    except Exception as exc:  # analytics must never break the flow
        log.warning("override_event_failed", field=field, error=str(exc))

    try:
        ref = propose_refinement_from_override(
            field=field, old_value=req.old_value, new_value=req.new_value, kind=req.kind,
        )
        refinement_id = feedback_repo.save_refinement(ref)  # always status='proposed'
    except Exception as exc:
        log.warning("override_refinement_failed", field=field, error=str(exc))

    return {
        "captured": event_recorded or refinement_id is not None,
        "event_recorded": event_recorded,
        "refinement_id": refinement_id,
        "refinement_status": "proposed",  # never 'accepted' — human-gated by design
    }


def _owner_dashboard(target: Any, *, dashboard: dict[str, Any] | None = None) -> dict[str, Any]:
    """The owner-facing dashboard payload: the raw roll-up enriched with a per-task
    ``dev_brief`` (Developer Handoff) and the client's stable ``share_token`` (so the UI
    can build the read-only /share/<token> link). ``dashboard`` reuses an already-fetched
    roll-up (e.g. the one a status update returns) instead of re-reading it."""
    from ..report.dev_brief import attach_dev_briefs
    from ..storage.repos import milestones as milestones_repo

    dash = dashboard if dashboard is not None else milestones_repo.get_dashboard(target.id)
    attach_dev_briefs(dash, origin=target.domain, cms_type=target.cms_type)
    dash["share_token"] = milestones_repo.ensure_share_token(target.id)
    return dash


def _client_for(domain: str) -> Any:
    """The target row for ``domain``, or 404. Shared by every milestone endpoint."""
    from ..storage.repos import targets as targets_repo

    target = targets_repo.by_domain(domain)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no client for domain {domain}")
    return target


def _assert_plan_access(target: Any, user: Any) -> None:
    """Authorize a MUTATION of this client's implementation plan.

    These routes take only ``{domain}`` and sit behind the shared service key, which the
    web proxy injects on every anonymous browser request — so without this check any
    visitor who knew a customer's domain could trigger a crawl of that site, flip their
    milestones, or revoke their live Developer Handoff link.

    An anonymously-created plan has no owner and stays open (unchanged signed-out flow).
    Once a logged-in user has synced it, only that user may mutate it."""
    from ..storage.repos import milestones as milestones_repo

    owner = milestones_repo.owner_of(target.id)
    if owner is None:
        return
    if user is None or str(user.id) != owner:
        raise HTTPException(status_code=403, detail="this plan belongs to another account")


@app.post("/api/milestones")
def sync_milestones(
    req: MilestoneSyncRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Persist a generated plan as the client's implementation milestones and return the
    dashboard. Idempotent: re-syncing keeps existing per-task progress and any
    crawl-verified status (stable task ids), only refreshing descriptions.

    A logged-in sync CLAIMS the plan (``owner_user_id``); afterwards only that user can
    mutate it. Anonymous syncs leave it unowned, as before."""
    from ..report.milestones import plan_to_milestones
    from ..storage.repos import milestones as milestones_repo
    from ..storage.repos import targets as targets_repo

    domain = req.domain.strip()
    if not domain:
        raise HTTPException(status_code=422, detail="domain is required")
    # Only persist a recognised platform — never overwrite a known CMS with 'unknown'
    # on a later re-sync (upsert COALESCE-preserves the prior value when this is None).
    cms = req.cms_type if req.cms_type in ("wordpress", "shopify") else None
    target = targets_repo.upsert(req.name or domain, domain, "client", cms_type=cms)
    _assert_plan_access(target, user)
    milestones_repo.sync_plan(
        target.id, plan_to_milestones(req.plan), owner_user_id=(str(user.id) if user else None)
    )
    return _owner_dashboard(target)


@app.get("/api/milestones")
def get_milestones(domain: str) -> dict[str, Any]:
    """The implementation dashboard for a domain (milestones + tasks + progress). Returns
    an empty dashboard when the domain has no plan persisted yet (not a 404), so the UI
    can show the 'build your plan to start tracking' state."""
    from ..storage.repos import targets as targets_repo

    target = targets_repo.by_domain(domain)
    if target is None:
        return {"milestones": [], "progress": {"total": 0, "verified": 0, "in_progress": 0, "pct": 0}}
    return _owner_dashboard(target)


@app.post("/api/milestones/task")
def update_milestone_task(
    req: MilestoneTaskUpdate, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Owner's manual status toggle for one task (Pending / In Progress / Verified).
    Returns the recomputed dashboard."""
    from ..storage.repos import milestones as milestones_repo

    target = _client_for(req.domain)
    _assert_plan_access(target, user)
    dash = milestones_repo.set_task_status(target.id, req.task_key, req.status)
    if dash is None:
        raise HTTPException(status_code=404, detail=f"no task {req.task_key}")
    return _owner_dashboard(target, dashboard=dash)


@app.post("/api/milestones/verify")
async def verify_milestones(
    req: MilestoneVerifyRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Run the verification crawl now ('Check my site') — discover the live site, detect
    which pending milestone artifacts are now present, auto-verify them, and return the
    refreshed dashboard + a summary of what flipped. Needs network + a live DB.

    The FIRST run for a client is a baseline: the plan is generated crawl-free, so it
    recommends pages the site may already have, and those are reported as ``already_live``
    rather than credited as newly published work (see ``pipeline.milestone_audit``)."""
    import asyncio

    from ..crawl.discovery import discover, seed_url
    from ..pipeline.milestone_audit import should_verify, verify_client_milestones

    target = await asyncio.to_thread(_client_for, req.domain)
    await asyncio.to_thread(_assert_plan_access, target, user)

    # Decide BEFORE paying for a full site crawl. This used to run discovery first and only
    # then notice there was nothing to verify (or that verification was switched off),
    # making the user wait through a real crawl to be told "nothing new is live yet".
    if not await asyncio.to_thread(should_verify, target.id):
        summary = await verify_client_milestones(target.id, target.domain)
        return {"summary": summary, "dashboard": await asyncio.to_thread(_owner_dashboard, target)}

    # Crawl the canonical domain, not the raw typed string: `example.com/pricing` would
    # otherwise seed discovery at an inner page and under-detect the rest of the site.
    domain = seed_url(target.domain)
    discovery = await discover(domain)
    summary = await verify_client_milestones(
        target.id, domain, discovered_slugs=[d.url for d in discovery.urls],
    )
    # psycopg2 + the dev_brief render are blocking; this is the one async route in the
    # family, so keep them off the event loop the concurrent requests share.
    return {"summary": summary, "dashboard": await asyncio.to_thread(_owner_dashboard, target)}


@app.post("/api/share/rotate")
def rotate_share(
    req: ShareRotateRequest, user: User | None = Depends(get_optional_user)
) -> dict[str, Any]:
    """Revoke the client's current Developer Handoff link and issue a fresh one (owner
    action — AUTHENTICATED; the guard only exempts the read-only GET under /api/share/).
    The old /share/<token> link stops resolving immediately. Returns the new token so the
    UI can rebuild every handoff link/textarea optimistically.

    Owner-gated: revoking another account's live handoff link is the most destructive of
    these mutations, so it takes the same ownership check as the rest."""
    from ..storage.repos import milestones as milestones_repo

    target = _client_for(req.domain)
    _assert_plan_access(target, user)
    return {"share_token": milestones_repo.rotate_share_token(target.id)}


@app.get("/api/share/{token}")
def shared_plan(token: str) -> dict[str, Any]:
    """Read-only, UNAUTHENTICATED view of a client's implementation plan — the Developer
    Handoff link. The share token in the path is the only credential (see require_api_key's
    /api/share/ exemption). Returns the dashboard (with per-task dev briefs) plus the
    business name/domain for the page header. 404 if the token is unknown or revoked."""
    from ..report.dev_brief import attach_dev_briefs
    from ..storage.repos import milestones as milestones_repo

    client = milestones_repo.client_for_token(token)
    if client is None:
        raise HTTPException(status_code=404, detail="this share link is invalid or has been revoked")
    dash = milestones_repo.get_dashboard(client["id"])
    attach_dev_briefs(dash, origin=client["domain"], cms_type=client.get("cms_type"))
    return {"business_name": client["name"], "domain": client["domain"], **dash}


def require_admin_key(request: Request) -> None:
    """Guard for routes that can MINT entitlements or read another user's data.

    The service ``X-API-Key`` is NOT sufficient here. ``web/app/api/[...path]/route.ts`` is a
    catch-all proxy that injects that key into every ``/api/*`` request it forwards, so any
    visitor's browser can present it — it authenticates the proxy, not the person. Gating
    ``/api/entitlements/grant`` on it alone let anyone POST themselves ``all_packs`` from the
    devtools console and walk through the entire CH-02a gate and CH-02b paywall for free.

    Fails CLOSED: if no admin key is configured but the service key IS, these routes are
    disabled (503) rather than open. Fully-open local dev (neither key set) still works."""
    from ..settings import get_settings

    cfg = get_settings().api
    if cfg.admin_key:
        if request.headers.get("x-admin-key") != cfg.admin_key:
            raise HTTPException(status_code=403, detail="admin credential required")
        return
    if cfg.auth_key:  # deployed posture, no admin credential → refuse rather than expose
        raise HTTPException(
            status_code=503,
            detail="admin routes are disabled: set AEO__API__ADMIN_KEY to enable them",
        )


@app.post("/api/entitlements/grant")
def grant_entitlement(req: GrantRequest, _: None = Depends(require_admin_key)) -> dict[str, Any]:
    """Manually grant a pack entitlement (v5 CH-02b promo/manual path; Stripe is the paid
    path). Upserts the app_users row the FK requires, then the entitlement.

    **ADMIN-gated** (``X-Admin-Key``), deliberately NOT the service ``X-API-Key`` — see
    :func:`require_admin_key`. ``scope='pack'`` requires ``pack_index``."""
    from ..reference.domain_config import normalize_domain
    from ..storage.repos import entitlements as entitlements_repo

    if req.scope == "pack" and req.pack_index is None:
        raise HTTPException(status_code=422, detail="scope='pack' requires pack_index")
    # Canonicalize the domain the SAME way the overview does (its `canon`), so grant-time
    # and future check-time (P4) key agree.
    domain = normalize_domain(req.domain) or req.domain.strip().lower()
    try:
        row = entitlements_repo.grant(
            str(req.user_id), domain, scope=req.scope,
            pack_index=req.pack_index, source=req.source, expires_at=req.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"granted": True, "entitlement": row}


@app.get("/api/entitlements")
def list_entitlements(
    domain: str, user_id: UUID | None = None, user: User = Depends(get_current_user)
) -> dict[str, Any]:
    """The CALLER's currently-valid entitlements for a domain.

    Previously took an arbitrary ``user_id`` behind the service key alone, which — through
    the key-injecting proxy — let anyone enumerate any user's grants. The subject is now the
    verified JWT; an explicit ``user_id`` is accepted only when it matches (so existing
    callers keep working) and 403s otherwise. ``domain`` is canonicalized to match how grants
    are stored."""
    from ..reference.domain_config import normalize_domain
    from ..storage.repos import entitlements as entitlements_repo

    if user_id is not None and str(user_id) != user.id:
        raise HTTPException(status_code=403, detail="you can only read your own entitlements")
    canon = normalize_domain(domain) or domain.strip().lower()
    return {"entitlements": entitlements_repo.list_for_user_domain(user.id, canon)}


@app.get("/api/auth/me")
def auth_me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The authenticated user (v5 CH-07). Requires a valid Supabase JWT; 401 otherwise.
    Also the frontend's post-login call that provisions app_users + claims the aeo_sid
    session (cookie-sourced, in get_current_user)."""
    return {"id": user.id, "email": user.email}


@app.post("/api/entitlements/redeem")
def redeem_promo(req: RedeemRequest, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Redeem a promo code to unlock a domain's packs (v5 CH-02b monetization stub). The
    user is taken ONLY from the verified JWT — never the body — so no one can unlock for
    another account. A valid code grants ``all_packs`` (source='promo'); an unknown code is
    a 422. Redemption is disabled (all codes invalid) when no promo codes are configured."""
    from ..reference.domain_config import normalize_domain
    from ..settings import get_settings
    from ..storage.repos import entitlements as entitlements_repo

    code = req.code.strip()
    if not code or code not in get_settings().auth.promo_code_set:
        raise HTTPException(status_code=422, detail="invalid or expired promo code")
    domain = normalize_domain(req.domain) or req.domain.strip().lower()
    if not domain:
        raise HTTPException(status_code=422, detail="a domain is required")
    row = entitlements_repo.grant(user.id, domain, scope="all_packs", source="promo")
    return {"unlocked": True, "domain": domain, "entitlement": row}


@app.post("/api/checkout/pack")
def checkout_pack(req: CheckoutRequest, request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Start a Stripe Checkout Session to buy ONE pack (v5 CH-02b, flat price per pack).

    Login is required and the buyer is taken ONLY from the verified JWT — the session's
    metadata is stamped server-side, so nobody can pay a pack into another account. Already
    -unlocked packs 409 rather than charging twice, and Pack 1 is free so it is never sold."""
    from ..payments.stripe import PaymentsError, create_pack_checkout, payments_enabled
    from ..reference.domain_config import normalize_domain

    if not payments_enabled():
        raise HTTPException(status_code=503, detail="payments are not configured")
    if req.pack_index <= 1:
        raise HTTPException(status_code=422, detail="Pack 1 is free")

    domain = normalize_domain(req.domain) or req.domain.strip().lower()
    if not domain:
        raise HTTPException(status_code=422, detail="a domain is required")

    # Don't charge for something the buyer already has (a re-click, or a promo/manual grant).
    from ..entitlements.logic import is_pack_locked, resolve_unlock_state
    from ..storage.repos import entitlements as entitlements_repo

    all_packs, unlocked = resolve_unlock_state(entitlements_repo.list_for_user_domain(user.id, domain))
    if not is_pack_locked(req.pack_index, unlocked_pack_indices=unlocked, all_packs=all_packs):
        raise HTTPException(status_code=409, detail="you already have this pack")

    origin = str(request.base_url).rstrip("/")
    try:
        session = create_pack_checkout(
            user_id=user.id, email=user.email, domain=domain,
            pack_index=req.pack_index, origin=origin,
        )
    except PaymentsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"checkout_url": session["url"], "session_id": session["id"]}


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Stripe's payment callback — the ONLY thing that turns money into an entitlement.

    Exempt from the X-API-Key guard (Stripe cannot send it) and authenticated instead by
    the HMAC signature over the RAW body, checked before the JSON is parsed. Always 200s on
    a verified event, including ones we ignore: a non-2xx makes Stripe retry for days, and
    an unrelated event type is not an error. Replays are safe — the grant upserts."""
    from ..logging import get_logger
    from ..payments.stripe import grant_from_event, verify_webhook

    raw = await request.body()
    try:
        event = verify_webhook(raw, request.headers.get("stripe-signature"))
    except ValueError as exc:
        # 400 (not 401) so Stripe surfaces it in the dashboard as a delivery failure.
        get_logger(__name__).warning("stripe_webhook_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    try:
        row = grant_from_event(event)
    except Exception as exc:  # a DB blip must not make Stripe give up on the event
        raise HTTPException(status_code=500, detail="could not apply the event") from exc
    return {"received": True, "granted": bool(row)}


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    """The retention dashboard (Block F): DAU series + return-rate + quick-win
    completion + the recommendation-implementation rate (the real 'did it work?'
    signal, joined from the Retention Engine's outcomes). Needs a live DB."""
    from ..storage.repos import events as events_repo

    return events_repo.metrics()


@app.get("/api/eval/overrides")
def eval_overrides() -> dict[str, Any]:
    """Offline-eval export (Task 7): the captured user overrides of LLM/system suggestions
    — (suggested → chosen) pairs for fine-tuning/eval. Gated like the rest of /api/* when
    an auth key is configured; internal-only by intent (no session_id is exposed)."""
    from ..storage.repos import events as events_repo

    rows = events_repo.export_overrides()
    return {
        "count": len(rows),
        "overrides": [
            {"metadata": r.get("metadata", {}), "url": r.get("url"), "at": r.get("created_at")}
            for r in rows
        ],
    }


# ── persisted, resumable plan (B1) ──────────────────────────────────────────────


@app.post("/api/plan-state")
def create_plan_state(req: PlanStateCreate) -> dict[str, Any]:
    """Persist the interactive plan so progress survives a device switch and earns a
    resumable ``/plan/<id>`` link. Returns the minted id."""
    from ..storage.repos import plan_state as plan_state_repo

    pid = plan_state_repo.create(
        plan=req.plan, profile=req.profile, session_id=(req.session_id or None),
        run_id=req.run_id, business_name=req.business_name, domain=req.domain,
        score_snapshot=req.score, done_task_ids=req.done_task_ids,
    )
    return {"id": pid}


@app.get("/api/plan-state")
def resume_plan_state(session_id: str | None = None) -> dict[str, Any]:
    """The newest saved plan for a returning session — powers the homepage 'resume'
    banner. Returns ``{"id": null}`` (200) when the session is absent/blank or has no saved
    plan yet, so the frontend never has to treat 'nothing to resume' as an error."""
    from ..storage.repos import plan_state as plan_state_repo

    sid = (session_id or "").strip()
    row = plan_state_repo.latest_for_session(sid) if sid else None
    if not row:
        return {"id": None}
    return {"id": row["id"], "business_name": row.get("business_name"), "domain": row.get("domain")}


# The /plan/<id> link is public, so the response is an explicit allowlist — never the
# raw row — to keep the creator's session_id off the wire.
_PLAN_STATE_PUBLIC = (
    "id", "run_id", "business_name", "domain", "plan", "profile",
    "score_snapshot", "done_task_ids", "created_at", "updated_at",
)


@app.get("/api/plan-state/{plan_id}")
def get_plan_state(plan_id: str) -> dict[str, Any]:
    """The saved plan behind a ``/plan/<id>`` link (plan + profile snapshot + progress).
    Returns an allowlisted view (no session_id) since the link is shareable. A transient
    DB outage reads as 503 (try again), distinct from a 404 (the plan really is gone)."""
    from ..storage.repos import plan_state as plan_state_repo

    try:
        row = plan_state_repo.get(plan_id)
    except Exception as exc:  # don't tell a user their valid link "expired" on a DB hiccup
        raise HTTPException(status_code=503, detail="plan store temporarily unavailable") from exc
    if not row:
        raise HTTPException(status_code=404, detail=f"no plan {plan_id}")
    return {k: row.get(k) for k in _PLAN_STATE_PUBLIC}


@app.put("/api/plan-state/{plan_id}")
def update_plan_state(plan_id: str, req: PlanProgressUpdate) -> dict[str, Any]:
    """Save progress (the completed-task set, optionally a refreshed score) for a plan."""
    from ..storage.repos import plan_state as plan_state_repo

    if not plan_state_repo.update_progress(plan_id, req.done_task_ids, req.score):
        raise HTTPException(status_code=404, detail=f"no plan {plan_id}")
    return {"ok": True}


@app.get("/api/site-freshness")
def site_freshness(domain: str) -> dict[str, Any]:
    """Has this domain been audited recently? Powers 'Last reviewed N days ago' + the
    use-existing/refresh affordance (Task 3, Slice 2b). Best-effort: any miss/error returns
    ``{fresh: false}`` so the wizard never breaks over it. ``has_report`` says whether a
    persisted site report exists to load instead of re-crawling."""
    from ..storage.repos import runs as runs_repo
    from ..storage.repos import site_reports as site_reports_repo

    dom = domain.strip()
    if not dom:
        return {"fresh": False}
    try:
        row = runs_repo.latest_for_domain(dom)
    except Exception:
        return {"fresh": False}
    if not row or not row.get("last_crawled_at"):
        return {"fresh": False}
    run_id = row["run_id"]
    try:
        has_report = site_reports_repo.for_run(run_id) is not None
    except Exception:
        has_report = False
    return {
        "fresh": True,
        "run_id": run_id,
        "last_crawled_at": row["last_crawled_at"],
        "status": row.get("status"),
        "has_report": has_report,
    }


@app.get("/api/recheck-status")
def recheck_status(domain: str) -> dict[str, Any]:
    """The 'Verified live' + 'predicted fixes' view (Spec #2 Slice C · Feature #2) for a
    domain's recommendation outcomes:

      * ``verified`` — re-crawl-confirmed ``implemented`` fixes (criterion-honest), each
        now carrying ``predicted_delta`` vs ``actual_delta`` (rubric points) so the
        estimate stays accountable once a fix lands;
      * ``pending``  — not-yet-done fixes with their PREDICTED '+X pts' lift (highest
        first), so the user can pick high-impact work before acting.

    Best-effort: any failure returns empty sets so the results UI never breaks over it.
    ``count`` remains the verified count (back-compat)."""
    from ..storage.repos import outcomes as outcomes_repo
    from ..validation.predict import PredictedLift

    empty: dict[str, Any] = {"verified": [], "pending": [], "count": 0}
    dom = domain.strip()
    if not dom:
        return empty
    try:
        verified_rows = outcomes_repo.implemented_for_domain(dom)
        pending_rows = outcomes_repo.pending_fixes_for_domain(dom)
    except Exception:  # surfacing fixes must never break the results view
        return empty

    def _f(value: Any) -> float | None:
        return float(value) if value is not None else None

    verified = []
    for r in verified_rows:
        bt, dt = r.get("baseline_tier"), r.get("detected_tier")
        actual = (int(dt) - int(bt)) if bt is not None and dt is not None else None
        verified.append(
            {
                "url": r["url_normalized"],
                "criterion": r.get("criterion"),
                "detected_at": r.get("detected_at"),
                "predicted_delta": _f(r.get("predicted_delta")),
                "actual_delta": actual,
            }
        )

    pending = []
    for r in pending_rows:
        pd = r.get("predicted_delta")
        basis = r.get("predicted_basis") or ("simulated" if pd is not None else "unknown")
        predicted = PredictedLift(
            point=_f(pd),
            low=_f(r.get("predicted_low")),
            high=_f(r.get("predicted_high")),
            basis=basis,
        )
        pending.append(
            {
                "url": r["url_normalized"],
                "criterion": r.get("criterion"),
                "action_required": r.get("action_required") or "",
                "predicted": predicted.model_dump(),
            }
        )

    return {"verified": verified, "pending": pending, "count": len(verified)}
