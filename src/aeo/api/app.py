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

from datetime import UTC
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from ..intelligence.brief import plan_from_brief
from ..reference.business_input import BusinessInput
from ..reference.competitor_patterns import CompetitorPatterns
from ..reference.framework import Framework, build_framework, load_framework
from ..reference.framework_bootstrap import bootstrap_framework, framework_file_path
from ..reference.generator import generate_blueprint
from ..report.packager import build_asset_bundle, checklist_for, plan_for
from . import jobs as jobs_mod
from .jobs import JOBS


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
    from ..intelligence import DEAD, classify_intake
    from ..pipeline import Orchestrator
    from ..settings import get_settings

    cache = _cache_age(req.domain)
    result = await Orchestrator().dry_run(req.domain, max_urls=req.max_urls, pages=0, use_llm=req.use_llm)
    intake = get_settings().intake
    discovered = int(result.get("discovered") or 0)
    # The live profile path doesn't fetch body text, so the gate is page-count based.
    route = classify_intake(
        discovered, None,
        min_pages=intake.thin_site_min_pages, min_words=intake.thin_site_min_words,
    )
    prof = result.get("profile")
    if prof is None or route == DEAD:
        # Crawl found nothing usable → the no-website brief path is the right flow.
        return {
            "route": DEAD,
            "profile": None,
            "industry": None,
            "location": None,
            "discovered": discovered,
            "source": result.get("source"),
            "next": "/api/plan",
            **cache,
        }
    return {
        "route": route,  # 'rich' | 'thin'
        "profile": prof,
        "industry": prof.get("industry"),
        "location": prof.get("location"),
        "coverage": result["coverage"],
        "discovered": discovered,
        "source": result["source"],
        **cache,
    }


@app.post("/api/competitors/suggest")
def competitors_suggest(req: CompetitorSuggestRequest) -> dict[str, Any]:
    """Likely competitors for a business brief (name + category + location), so the UI
    can offer a pick-list instead of demanding URLs. Source is ``llm`` when suggestions
    were generated, ``unavailable`` when no LLM is configured — the frontend then falls
    back to manual entry only."""
    from ..nlp.llm import get_client
    from ..reference.competitor_discovery import discover_competitors

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    llm = get_client()
    if not llm.enabled:
        return {"competitors": [], "source": "unavailable"}
    result = discover_competitors(
        name,
        (req.domain or "").strip(),
        topic=(req.category or "").strip() or None,
        location=(req.location or "").strip() or None,
        count=req.count,
        llm=llm,
        head_check=None if req.verify else (lambda _domain: True),
    )
    return {
        "competitors": [{"name": c.name, "domain": c.domain} for c in result.verified],
        "source": "llm",
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
    job = JOBS.create("audit")
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
