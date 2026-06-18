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

import ipaddress
import json
import socket
from datetime import UTC
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from ..intelligence.brief import plan_from_brief
from ..reference.business_input import BusinessInput
from ..reference.competitor_patterns import CompetitorPatterns
from ..reference.framework import Framework, build_framework, load_framework
from ..reference.framework_bootstrap import bootstrap_framework, framework_file_path
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
    if request.headers.get("x-api-key") != key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


app = FastAPI(title="AEO Pipeline API", version="0.2.0", dependencies=[Depends(require_api_key)])


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


def _framework_and_llm(brief: BusinessInput, use_llm: bool) -> tuple[Framework, Any]:
    """A brief-tailored framework (curated file if present, else an in-memory bootstrap
    skeleton — LLM-tailored when enabled) + the resolved LLM client."""
    from ..nlp.llm import get_client

    llm = get_client() if use_llm else None
    key = brief.key()
    if framework_file_path(key).exists():
        return load_framework(key), llm
    data = bootstrap_framework(key, llm=llm, topic=brief.topic_hint(), category=brief.category)
    return build_framework(data), llm


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
    """Scenario 1: a business brief → ideal-site blueprint + no_website strategy, no crawl."""
    brief = _brief(req)
    framework, llm = _framework_and_llm(brief, req.use_llm)
    return plan_from_brief(brief, framework=framework, llm=llm).to_dict()


@app.post("/api/blueprint")
def blueprint(req: BlueprintRequest) -> dict[str, Any]:
    """Generate the ideal-site blueprint for a topic/domain (deterministic; LLM enriches)."""
    from ..nlp.llm import get_client

    llm = get_client() if req.use_llm else None
    if req.domain:
        from ..reference.domain_config import normalize_domain

        key = normalize_domain(req.domain) or req.domain
        framework = (
            load_framework(key)
            if framework_file_path(key).exists()
            else build_framework(bootstrap_framework(key, llm=llm, topic=req.topic, category=req.category))
        )
    else:
        framework = load_framework()
    bp = generate_blueprint(
        topic=req.topic or framework.topic, framework=framework,
        patterns=CompetitorPatterns(), llm=llm,
    )
    return bp.to_jsonb()


@app.post("/api/deliverables")
def deliverables(req: DeliverablesRequest) -> dict[str, Any]:
    """Build the developer-ready asset bundle from a brief and return it inline (the
    frontend renders / offers each asset for download)."""
    from ..report.strategy import build_strategy

    brief = _brief(req)
    framework, llm = _framework_and_llm(brief, req.use_llm)
    plan_result = plan_from_brief(brief, framework=framework, llm=llm)
    bundle = build_asset_bundle(
        blueprint=plan_result.blueprint, coverage=plan_result.coverage,
        profile=plan_result.profile.to_dict(), origin=brief.domain or brief.key(),
        llm=llm, draft_limit=req.draft_limit,
        builder_mode=req.builder_mode, business=_business_dict(brief),
    )
    # #10 — the prioritized plan as structured JSON: phased, quick-wins flagged, each
    # task carrying current_state/action_required/how_to + AI-vs-human prompts. This
    # is what the interactive in-app checklist renders.
    plan = plan_for(
        blueprint=plan_result.blueprint, coverage=plan_result.coverage,
        builder_mode=req.builder_mode, business=_business_dict(brief),
    )
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


@app.post("/api/profile")
async def profile(req: ProfileRequest) -> dict[str, Any]:
    """Classify a LIVE site (reuses the zero-DB dry-run path) and BRANCH ON CRAWL QUALITY.

    The URL-first intake (#1/#2/#3) leans on this: a content-rich/thin site returns its
    SiteProfile with the crawl-derived ``industry`` + ``location`` (so the wizard prefills
    instead of asking), while a dead/unreachable crawl returns ``route='dead'`` pointing at
    the no-website brief path (``/api/plan``) — never a 502, so the flow always continues."""
    import asyncio

    from ..intelligence import DEAD, classify_intake
    from ..intelligence.industry import resolve_wikidata_industry
    from ..intelligence.site_facts import SiteFacts, gather_site_facts
    from ..pipeline import Orchestrator
    from ..settings import get_settings

    cache = _cache_age(req.domain)
    # Crawl the homepage + key pages for Location / what-you-offer / on-site competitors,
    # and resolve a SPECIFIC industry vertical from Wikidata — all concurrently with the
    # structural dry-run profile so the wizard prefills in one round.
    facts_task = asyncio.create_task(gather_site_facts(req.domain))
    wikidata_task = asyncio.create_task(resolve_wikidata_industry(req.domain))
    result = await Orchestrator().dry_run(req.domain, max_urls=req.max_urls, pages=0, use_llm=req.use_llm)
    try:
        facts: SiteFacts = await facts_task
    except Exception:  # facts are best-effort enrichment — never fail the profile over them
        facts = SiteFacts()
    try:
        wikidata_industry = await wikidata_task
    except Exception:
        wikidata_industry = None
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
    industry = wikidata_industry or facts.industry or (prof.get("industry") if prof else None)
    industry_source = (
        "wikidata" if wikidata_industry else "crawl" if facts.industry else "model" if industry else None
    )
    if prof is None or route == DEAD:
        # Crawl found nothing usable → the no-website brief path is the right flow.
        return {
            "route": DEAD,
            "profile": None,
            "industry": industry,
            "industry_source": industry_source,
            "location": facts.location,
            "services": facts.services,
            "competitors": facts.competitors,
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
        # Prefer the content-derived location (address/footer/schema) over the URL-path guess.
        "location": facts.location or prof.get("location"),
        "services": facts.services,
        "competitors": facts.competitors,
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
