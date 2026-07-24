"""
The free URL-first overview (v5 CH-09 + the CH-16 slice-1 boundary).

One paste of a URL yields, without signup and without a persisted run:

  * the structural site profile + coverage from ``Orchestrator.dry_run`` (zero-DB,
    no-LLM — the same fast path ``/api/profile`` rides),
  * the five skill scores for the HOMEPAGE, computed in memory from the HTML the
    site-facts prefill already fetched (no extra network, bot-wall fallback included),
  * an impact-ordered pack preview (Pack 1 open, deeper packs shown locked — real
    gating arrives with auth in P4),
  * the on-site competitor names (honest empty state — most SMB sites name none),
  * industry/location/offer enrichment (crawl → Wikidata → model, mirroring
    ``/api/profile``'s precedence).

Results are cached per domain (``OVERVIEW_CACHE_TTL_SEC``) so repeated pastes are
free — the §9.4 cost ceiling together with the per-IP daily cap enforced in the API
layer. The cache is in-process (mirrors ``industry._PROFILE_CACHE``): a multi-worker
deployment re-fetches per worker, which is acceptable at free-tier scale.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from ..intelligence import DEAD, classify_intake
from ..intelligence.industry import WikidataProfile, resolve_wikidata_profile
from ..intelligence.site_facts import SiteFacts, first_clause, gather_site_facts_with_docs
from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..scoring.aggregator import score_page
from ..scoring.skills import build_skill_scores
from ..settings import LLMCfg, get_settings
from ..storage.models import FetchedPage
from ..utils.url import normalize
from .packs import build_packs
from .stages import ExtractStage

log = get_logger(__name__)

OVERVIEW_CACHE_TTL_SEC = 86400  # §9.4 resolved: same-domain re-pastes are free for a day

# Mirrors /api/profile's Wikidata wait: the lookup races the crawl, so this cap only
# bounds the slow-Wikimedia tail.
_WIKIDATA_WAIT_BUDGET_SEC = 8.0

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_MAX = 500  # cheap bound; evict expired entries once the map grows past it


def _cache_key(domain: str) -> str:
    from ..reference.domain_config import normalize_domain

    return normalize_domain(domain) or domain.strip().lower()


def cached_overview(domain: str) -> dict[str, Any] | None:
    """The cached overview for a domain, or None. Cache hits are flagged so the UI can
    say "from earlier today" and the rate limiter can skip counting them."""
    hit = _CACHE.get(_cache_key(domain))
    if hit is None or time.time() - hit[0] >= OVERVIEW_CACHE_TTL_SEC:
        return None
    return {**hit[1], "cached": True}


def _store(domain: str, payload: dict[str, Any]) -> None:
    now = time.time()
    if len(_CACHE) > _CACHE_MAX:
        # Drop expired entries first…
        for key in [k for k, (ts, _) in _CACHE.items() if now - ts >= OVERVIEW_CACHE_TTL_SEC]:
            _CACHE.pop(key, None)
        # …then, if still over the cap (many distinct domains within one TTL window),
        # evict oldest-first so _CACHE_MAX is a real memory bound, not just a TTL bound.
        if len(_CACHE) >= _CACHE_MAX:
            for key, _ in sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: len(_CACHE) - _CACHE_MAX + 1]:
                _CACHE.pop(key, None)
    _CACHE[_cache_key(domain)] = (now, payload)


def _homepage_skills(
    docs: list[Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Any]:
    """Score the homepage in memory from the already-fetched HTML: extract → 10-criterion
    score (deterministic — a disabled LLM client keeps this synchronous path off any
    model) → the 5-skill derived layer. Returns (skills payload, homepage summary, the
    extraction bundle — reused by the CH-14 AI-visibility check)."""
    if not docs:
        return None, None, None
    home = docs[0]
    page = FetchedPage(
        url=home.url,
        url_normalized=normalize(home.url),
        success=True,
        http_status=200,
        fetch_duration_ms=0,
        html=home.html,
        markdown="",
        title="",
        meta_description="",
        error=None,
        content_hash=None,
    )
    bundle = ExtractStage().run(page, 0, None)  # page_id=0 — never persisted
    page_score = score_page(bundle, 0, llm=LLMClient(LLMCfg(enabled=False)))
    skills = build_skill_scores(page_score, bundle)
    homepage = {
        "url": home.url,
        "aeo_total": page_score.total,
        "aeo_max": page_score.max_possible,
        "priority_tier": page_score.priority_tier,
    }
    return skills, homepage, bundle


async def build_overview(domain: str, *, max_urls: int | None = None) -> dict[str, Any]:
    """Compose the free overview for a domain. Best-effort per part: a dead crawl still
    returns an honest ``route='dead'`` payload, a blocked homepage returns the structural
    profile with ``skills: null`` + a reason — never a 502 mid-funnel."""
    from urllib.parse import quote

    from ..pipeline import Orchestrator

    # The cached payload is keyed by the normalized host and served to every later caller
    # who pastes any equivalent form, so its display/link fields must be the CANONICAL
    # host — not the first requester's raw string (which could carry a scheme, path, or
    # tracking junk that then leaks into a stranger's overview + deeper link).
    canon = _cache_key(domain)

    facts_task = asyncio.create_task(gather_site_facts_with_docs(domain))
    wikidata_task = asyncio.create_task(resolve_wikidata_profile(domain))

    result = await Orchestrator().dry_run(
        domain, max_urls=max_urls, pages=0, use_llm=False, draft_samples=False
    )
    try:
        facts, docs = await facts_task
    except Exception:  # facts are enrichment — never fail the overview over them
        facts, docs = SiteFacts(), []
    wikidata = WikidataProfile()
    try:
        wikidata = await asyncio.wait_for(wikidata_task, timeout=_WIKIDATA_WAIT_BUDGET_SEC)
    except Exception:
        wikidata = WikidataProfile()

    intake = get_settings().intake
    discovered = int(result.get("discovered") or 0)
    route = classify_intake(
        discovered, None,
        min_pages=intake.thin_site_min_pages, min_words=intake.thin_site_min_words,
    )

    # Enrichment precedence mirrors /api/profile (industry: wikidata → crawl → model;
    # location: crawl → wikidata → model; offer: crawl → wikidata rungs → industry label).
    prof = result.get("profile")
    industry = wikidata.industry or facts.industry or (prof.get("industry") if prof else None)
    industry_source = (
        "wikidata" if wikidata.industry else "crawl" if facts.industry else "model" if industry else None
    )
    wikidata_location = (
        f"{wikidata.location}, {wikidata.country}"
        if wikidata.location and wikidata.country
        else wikidata.location or wikidata.country
    )
    location = facts.location or wikidata_location or (prof.get("location") if prof else None)
    services = facts.services or wikidata.offerings
    if not services and wikidata.description:
        clause = first_clause(wikidata.description)
        services = [clause] if clause else []
    if not services and industry:
        services = [industry]

    skills, homepage, home_bundle = (None, None, None)
    skills_unavailable_reason: str | None = None
    try:
        # Parse+score is CPU-bound (BeautifulSoup over a full homepage) — keep it off
        # the event loop so concurrent overview/profile requests aren't starved.
        skills, homepage, home_bundle = await asyncio.to_thread(_homepage_skills, docs)
    except Exception as exc:  # scoring is the payload's core but must degrade honestly
        log.warning("overview_homepage_score_failed", domain=domain, error=str(exc))
    if skills is None:
        skills_unavailable_reason = (
            "homepage_unreachable" if not docs else "homepage_scoring_failed"
        )

    # v5 CH-14: does the homepage get CITED by AI answer engines? Best-effort + bounded +
    # degradable — 'unavailable' (never a fake verdict) when Perplexity is unconfigured
    # (the default), so this adds zero cost/latency on the free tier unless ops enables it.
    # Skipped on a dead/unreadable site (the UI hides it there — don't spend a probe on it).
    is_dead = prof is None or route == DEAD
    ai_visibility: dict[str, Any] = {"status": "unavailable", "engine": "perplexity", "reason": "no_homepage"}
    if home_bundle is not None and not is_dead:
        try:
            from .ai_visibility import check_ai_visibility

            ai_visibility = await check_ai_visibility(home_bundle, docs[0].url)
        except Exception as exc:  # never fail the overview over the visibility check
            log.warning("overview_ai_visibility_failed", domain=domain, error=str(exc))
            ai_visibility = {"status": "unavailable", "engine": "perplexity", "reason": "error"}

    # Anonymous free tier: no grants → decorate_pack unlocks only Pack 1, locks the rest.
    # Routing through the shared resolver (not an inline pack_index>1) keeps the overview
    # and the authenticated pack API from ever drifting on the lock rule.
    from ..entitlements.logic import decorate_pack

    packs = [decorate_pack(pack.to_dict(), grants=[]) for pack in build_packs(result.get("ranking") or [])]

    cov = result.get("coverage") or {}
    coverage = {
        "pct": cov.get("pct"),
        "matched": cov.get("matched"),
        "total_nodes": cov.get("total_nodes"),
        "missing": cov.get("missing"),
        "top_missing": [
            {"slug": m.get("slug"), "title": m.get("title"), "priority": m.get("priority")}
            for m in (cov.get("top_missing") or [])[:5]
        ],
    } if cov else None

    # facts.competitors is list[dict] ({name, domain}); the free overview contract exposes
    # bare names (string[]) — extract them so the payload can't hand the frontend objects
    # (which would crash OverviewView's `{n}` render).
    competitor_names = [
        str(c.get("name")).strip()
        for c in (facts.competitors or [])
        if isinstance(c, dict) and c.get("name")
    ][:6]
    payload: dict[str, Any] = {
        "domain": canon,
        "route": DEAD if (prof is None or route == DEAD) else route,
        "cached": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "site": {
            "industry": industry,
            "industry_source": industry_source,
            "location": location,
            "services": services,
            "about": wikidata.description,
            "cms_type": facts.cms_type,
            "discovered": discovered,
            "source": result.get("source"),
        },
        "coverage": coverage,
        "homepage": homepage,
        "skills": skills,
        "skills_unavailable_reason": skills_unavailable_reason,
        "ai_visibility": ai_visibility,  # v5 CH-14 — cited by AI answer engines?
        "packs": packs,
        "competitors": {
            "names": competitor_names,
            "reason": None if competitor_names else "none_detected",
        },
        # review=1 lands the studio on the one-page review — every intake section
        # prefilled on a single page, deep audit behind its "Build my plan" CTA.
        # (autobuild=1 stays the unattended path used by saved-plan deep links.)
        "next": {"deeper": f"/studio?domain={quote(canon)}&review=1"},
    }
    _store(domain, payload)
    return payload
