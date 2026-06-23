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
import ipaddress
import json
import socket
import time
from datetime import UTC
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from ..intelligence.brief import plan_from_brief
from ..reference.business_input import BusinessInput
from ..reference.competitor_patterns import CompetitorPatterns
from ..reference.framework import Framework
from ..reference.generator import generate_blueprint
from ..report.packager import build_asset_bundle, checklist_for, plan_for
from . import jobs as jobs_mod
from .jobs import JOBS

# Bounds for the persisted-plan payloads (B1) — keep a stored plan/profile blob and its
# completed-task set from growing unboundedly through the public plan-state endpoints.
_MAX_JSON_BYTES = 1 * 1024 * 1024
_MAX_DONE_IDS = 5000
_MAX_TASK_ID_LEN = 256
# Bound concurrent deep audits — each spawns a crawl worker thread, so cap the blast radius.
_MAX_CONCURRENT_AUDITS = 4

# Wikidata "About you" enrichment is DISABLED in /api/profile. The WDQS lookup is a full
# P856 scan that runs ~38–50s uncontended — it never returns inside this endpoint's budget,
# so the merge below was inert in production (location/about always fell back to the crawl).
# The resolver (intelligence.industry.resolve_wikidata_profile) is left intact behind this
# flag rather than deleted: flip to True only with a sub-budget retrieval path (e.g. the
# SPARQL→QID→wbgetentities hybrid) AND after confirming the actual onboarding audience has
# Wikidata entities (notable firms do; local SMBs don't). See the analysis thread.
_WIKIDATA_ENRICHMENT_ENABLED = False


def _assert_crawlable_host(domain: str) -> None:
    """SSRF guard: resolve the target host and reject private/loopback/link-local/
    reserved addresses so an attacker can't point a crawl at internal infrastructure
    (e.g. cloud metadata at 169.254.169.254). Best-effort at the entry point — a
    deeper defense also revalidates each redirect hop in the crawler."""
    raw = domain.strip()
    host = urlparse(raw if "://" in raw else f"//{raw}").hostname or raw
    host = host.split(":")[0].strip()
    if not host:
        raise HTTPException(status_code=400, detail="invalid domain")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise HTTPException(status_code=400, detail="domain does not resolve") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
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
    if request.headers.get("x-api-key") != key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


app = FastAPI(title="AEO Pipeline API", version="0.2.0", dependencies=[Depends(require_api_key)])


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


_RATE = _RateLimiter()


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
    auth, so an attacker can't hammer the key check either."""
    from ..settings import get_settings

    cfg = get_settings().api
    path = request.url.path
    limited = cfg.rate_limit > 0 and path.startswith("/api/") and path != "/api/health"
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
            allow_headers=["Content-Type", "X-API-Key"],
        )


_install_cors(app)


# ── request models ────────────────────────────────────────────────────────────


class BriefRequest(BaseModel):
    name: str
    domain: str | None = None
    category: str | None = None
    topic: str | None = None
    location: str | None = None
    services: list[str] = []
    competitors: list[str] = []
    goals: list[str] = []
    use_llm: bool = True


class DeliverablesRequest(BriefRequest):
    draft_limit: int = 10
    # Who's building the site — shapes the kit (see report.packager): "dev" keeps the
    # original developer bundle; diy/ai/hire produce the owner-facing packs.
    builder_mode: Literal["dev", "diy", "ai", "hire"] = "dev"


class BlueprintRequest(BaseModel):
    topic: str | None = None
    domain: str | None = None
    category: str | None = None
    use_llm: bool = True


class ProfileRequest(BaseModel):
    domain: str
    max_urls: int | None = None
    use_llm: bool = True


class AuditRequest(BaseModel):
    domain: str
    name: str | None = None
    # R2-2 re-crawl: bypass the fingerprint skip gate so unchanged pages are re-read.
    force: bool = False


class CompetitorSuggestRequest(BaseModel):
    name: str
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
    from ..intelligence.site_facts import SiteFacts, gather_site_facts
    from ..pipeline import Orchestrator
    from ..settings import get_settings

    cache = _cache_age(req.domain)
    # Crawl the homepage + key pages for Location / what-you-offer / on-site competitors,
    # concurrently with the structural dry-run profile so the wizard prefills in one round.
    # Wikidata enrichment (HQ location/country/products/description) is gated OFF — see
    # _WIKIDATA_ENRICHMENT_ENABLED: the WDQS lookup can't return inside this budget, so the
    # merge stays code-complete but the network call isn't even spawned.
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
            wikidata = await wikidata_task
        except Exception:  # Wikidata is best-effort enrichment — never fail the profile over it
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
    # What you offer, best source first: crawl offerings (schema.org / service pages) →
    # Wikidata products produced (P1056).
    services = facts.services or wikidata.offerings
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


@app.post("/api/competitors/suggest")
async def competitors_suggest(req: CompetitorSuggestRequest) -> dict[str, Any]:
    """Likely competitors for a business brief (name + category + location), so the UI
    can offer a pick-list instead of demanding URLs. Source is ``llm`` when the LLM
    generated suggestions; ``onsite`` when (no LLM) we mined the site's own
    comparison/alternatives pages; ``unavailable`` when neither yielded anything."""
    from ..intelligence.site_facts import gather_site_facts
    from ..nlp.llm import get_client
    from ..reference.competitor_discovery import discover_competitors

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    # The SHARED client: on AEO__LLM__PROVIDER=cloud this is Gemini, so production gets
    # cloud latency for free. If a slow LOCAL model is the bottleneck here, point the burst
    # paths at a faster backend via AEO__LLM__BULK_PROVIDER rather than hardcoding a model
    # (domain verification is already parallelized; the LLM call is the remaining cost).
    llm = get_client()
    if not llm.enabled:
        # No LLM → best-effort on-site signals (comparison / alternatives pages).
        domain = (req.domain or "").strip()
        if domain:
            facts = await gather_site_facts(domain)
            if facts.competitors:
                return {"competitors": facts.competitors[: req.count], "source": "onsite"}
        return {"competitors": [], "source": "unavailable"}
    result = discover_competitors(
        name,
        (req.domain or "").strip(),
        topic=(req.category or "").strip() or None,
        location=(req.location or "").strip() or None,
        services=req.services,
        count=req.count,
        llm=llm,
        head_check=None if req.verify else (lambda _domain: True),
    )
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
    """Approve/reject gate: only a 'staged' run can be decided, and only a human does it."""
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    if row["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"run is {row['status']}, not staged")
    agent_runs_repo.set_status(run_id, decision)
    return {"run_id": run_id, "status": decision}


@app.post("/api/agent/run")
def agent_run_start(req: BriefRequest) -> dict[str, Any]:
    """Start an assistive agent run. The Planner stages a task graph for human review; nothing
    is published. Returns the run id to poll."""
    if req.domain:
        _assert_crawlable_host(req.domain)  # SSRF parity — the run may crawl this domain later
    from ..agents.runtime import start_agent_run

    row = start_agent_run(_brief(req).to_dict())
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


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@app.get("/api/agent/runs")
def agent_runs_list(status: str = "staged", limit: int = 50) -> dict[str, Any]:
    """Agent runs in a given status (default 'staged' — the review queue)."""
    from ..storage.repos import agent_runs as agent_runs_repo

    return {"runs": agent_runs_repo.list_by_status(status, limit=max(1, min(limit, 200)))}


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


@app.post("/api/milestones")
def sync_milestones(req: MilestoneSyncRequest) -> dict[str, Any]:
    """Persist a generated plan as the client's implementation milestones and return the
    dashboard. Idempotent: re-syncing keeps existing per-task progress and any
    crawl-verified status (stable task ids), only refreshing descriptions."""
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
    milestones_repo.sync_plan(target.id, plan_to_milestones(req.plan))
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
def update_milestone_task(req: MilestoneTaskUpdate) -> dict[str, Any]:
    """Owner's manual status toggle for one task (Pending / In Progress / Verified).
    Returns the recomputed dashboard."""
    from ..storage.repos import milestones as milestones_repo
    from ..storage.repos import targets as targets_repo

    target = targets_repo.by_domain(req.domain)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no client for domain {req.domain}")
    dash = milestones_repo.set_task_status(target.id, req.task_key, req.status)
    if dash is None:
        raise HTTPException(status_code=404, detail=f"no task {req.task_key}")
    return _owner_dashboard(target, dashboard=dash)


@app.post("/api/milestones/verify")
async def verify_milestones(req: MilestoneVerifyRequest) -> dict[str, Any]:
    """Run the verification crawl now ('Check my site') — discover the live site, detect
    which pending milestone artifacts are now present, auto-verify them, and return the
    refreshed dashboard + a summary of what flipped. Needs network + a live DB."""
    from ..crawl.discovery import discover
    from ..pipeline.milestone_audit import verify_client_milestones
    from ..storage.repos import targets as targets_repo

    target = targets_repo.by_domain(req.domain)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no client for domain {req.domain}")
    discovery = await discover(req.domain)
    summary = await verify_client_milestones(
        target.id, req.domain, discovered_slugs=[d.url for d in discovery.urls],
    )
    return {"summary": summary, "dashboard": _owner_dashboard(target)}


@app.post("/api/share/rotate")
def rotate_share(req: ShareRotateRequest) -> dict[str, Any]:
    """Revoke the client's current Developer Handoff link and issue a fresh one (owner
    action — AUTHENTICATED; the guard only exempts the read-only GET under /api/share/).
    The old /share/<token> link stops resolving immediately. Returns the new token so the
    UI can rebuild every handoff link/textarea optimistically."""
    from ..storage.repos import milestones as milestones_repo
    from ..storage.repos import targets as targets_repo

    target = targets_repo.by_domain(req.domain)
    if target is None:
        raise HTTPException(status_code=404, detail=f"no client for domain {req.domain}")
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
    """The 'Verified live' view (Spec #2 Slice C): recommendation outcomes a re-crawl has
    confirmed implemented for this domain. Honest by construction — only criterion-verified
    outcomes appear. Best-effort: any failure returns an empty set so the results UI never
    breaks over it."""
    from ..storage.repos import outcomes as outcomes_repo

    dom = domain.strip()
    if not dom:
        return {"verified": [], "count": 0}
    try:
        rows = outcomes_repo.implemented_for_domain(dom)
    except Exception:  # surfacing verified fixes must never break the results view
        return {"verified": [], "count": 0}
    verified = [
        {"url": r["url_normalized"], "criterion": r.get("criterion"), "detected_at": r.get("detected_at")}
        for r in rows
    ]
    return {"verified": verified, "count": len(verified)}
