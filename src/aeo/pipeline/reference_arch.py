"""
Reference-architecture orchestration — the DB-touching glue for the v4 generator
and Coverage Diff, kept out of the Orchestrator so that class stays readable.

Two best-effort steps run at the front of a site run, *isolated* so a failure
(no competitor data yet, generator hiccup, transient DB) logs and is skipped —
never aborting the crawl that follows:

  * :func:`generate_and_pin_blueprint` — build competitor patterns (L1) from the
    latest competitor extractions, synthesize a versioned blueprint, persist it
    (reuse-or-bump), and pin it to the run;
  * :func:`compute_and_persist_coverage` — diff the discovered/classified sitemap
    against the pinned blueprint and persist the site-level coverage findings.

The pure seam — :func:`discovered_pages` — is unit-tested directly; the DB calls
are exercised by the integration smoke test.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..crawl.prioritize import ScoredUrl
from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..processor.coverage_diff import CoverageDiffResult, DiscoveredPage, coverage_diff
from ..reference import Reference, load_reference
from ..reference.blueprint import normalize_slug
from ..reference.competitor_patterns import CompetitorPatterns, extract_patterns
from ..reference.domain_config import load_domain_config, normalize_domain
from ..reference.framework_bootstrap import resolve_framework
from ..reference.generator import generate_blueprint
from ..settings import get_settings
from ..storage.models import ExtractionBundle
from ..storage.repos import blueprints as blueprints_repo
from ..storage.repos import coverage as coverage_repo
from ..storage.repos import extractions as extractions_repo
from ..storage.repos import scores as scores_repo
from ..storage.repos.blueprints import StoredBlueprint

log = get_logger(__name__)


def _domain(url: str) -> str:
    host = (urlsplit(url).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def build_competitor_patterns(allowed_entities: list[str]) -> CompetitorPatterns:
    """L1: aggregate structural patterns from the latest competitor extractions.
    Empty (but valid) when no competitors have been crawled yet."""
    rows = scores_repo.latest_competitor_scores()
    pages: list[tuple[str, ExtractionBundle]] = []
    domains: set[str] = set()
    for row in rows:
        domains.add(_domain(row["url"]))
        bundle = extractions_repo.get(row["page_id"])
        if bundle is not None:
            pages.append((row["url"], bundle))
    return extract_patterns(pages, allowed_entities=allowed_entities, domains=sorted(domains))


def generate_and_pin_blueprint(
    run_id: int, *, topic: str | None = None, domain: str | None = None,
    llm: LLMClient | None = None,
) -> StoredBlueprint | None:
    """Generate (or reuse) the versioned blueprint and pin it to the run.
    Returns None when the generator is disabled.

    ``domain`` opts the run into per-domain onboarding (``config/domains/{domain}.yaml``):
    its ``topic`` / ``engine_target`` override the global settings for this run.
    Resolution order (highest wins): explicit ``topic`` arg → domain config →
    settings → framework default."""
    cfg = get_settings().reference_architecture
    if not cfg.enabled:
        return None
    dc = load_domain_config(domain) if domain else None
    # Per-site framework: curated per-domain file if present, else a bootstrapped
    # generic/LLM-tailored skeleton — never the shared cybersecurity seed for an
    # unconfigured site.
    framework = resolve_framework(domain, llm=llm, topic=topic or (dc.topic if dc else None))
    # The loaded framework is the topic authority (per-domain override wins); the
    # settings default is only a last resort when nothing more specific exists.
    topic = topic or (dc.topic if dc else None) or framework.topic or cfg.topic
    engine_target = (dc.engine_target if dc and dc.engine_target else None) or cfg.engine_target
    patterns = build_competitor_patterns(framework.required_entities)
    blueprint = generate_blueprint(
        topic=topic, framework=framework, patterns=patterns, llm=llm,
        engine_target=engine_target,
    )
    stored = blueprints_repo.save_versioned(blueprint)
    blueprints_repo.pin_run(run_id, stored.id)
    log.info(
        "blueprint_pinned", run_id=run_id, topic=topic, version=stored.blueprint.version,
        reused=stored.reused, generator=stored.blueprint.generator, nodes=len(stored.blueprint.sitemap),
        engine_target=engine_target, domain=normalize_domain(domain) if domain else None,
    )
    return stored


def discovered_pages(scored: list[ScoredUrl], reference: Reference) -> list[DiscoveredPage]:
    """Pure: map prioritized URLs (page-type from the prioritizer) to the Coverage
    Diff's view, classifying intent via the Reference Layer."""
    return [
        DiscoveredPage(
            url=s.url,
            slug=normalize_slug(s.url),
            page_type=s.page_type,
            intent=reference.classify_intent(s.url),
        )
        for s in scored
    ]


def compute_and_persist_coverage(
    run_id: int,
    stored: StoredBlueprint,
    scored: list[ScoredUrl],
    *,
    target_id: int | None,
    reference: Reference | None = None,
    domain: str | None = None,
    topic: str | None = None,
    llm: LLMClient | None = None,
    with_profile: bool = True,
) -> CoverageDiffResult:
    """Diff the discovered sitemap against the pinned blueprint and persist it.

    When ``with_profile`` (default), the SP-1 intelligence layer builds a
    :class:`SiteProfile` (site classification, business model, journey gaps, scenario +
    prioritized strategy) over the same discovered inventory and embeds it in the
    coverage ``detail`` JSONB under ``"site_profile"`` — so the site report can surface
    the consultant-facing strategy with no new table. Best-effort: a profile failure is
    logged and skipped, never aborting the coverage write."""
    reference = reference or load_reference()
    discovered = discovered_pages(scored, reference)
    result = coverage_diff(stored.blueprint, discovered)
    detail = result.to_detail()

    if with_profile:
        try:
            from ..intelligence import build_site_profile

            profile = build_site_profile(
                domain=domain or stored.blueprint.topic,
                discovered=scored,
                coverage=result,
                # Only a per-domain onboarding topic identifies the *site's* industry; the
                # blueprint topic is the seeded framework topic and must not leak here, or
                # every site reads as that one topic's industry. None -> business-model label.
                topic=topic,
                llm=llm,
            )
            detail["site_profile"] = profile.to_dict()
            log.info(
                "site_profile_built", run_id=run_id, scenario=profile.strategy.scenario.value,
                model=profile.business_intent.model.value, site_class=profile.classification.site_class.value,
            )
        except Exception as exc:  # never let the brain break the coverage write
            log.warning("site_profile_skipped", run_id=run_id, error=str(exc))

    coverage_repo.put(
        run_id,
        blueprint_id=stored.id,
        target_id=target_id,
        coverage_pct=result.coverage_pct,
        missing_count=len(result.missing),
        thin_count=len(result.thin_clusters),
        detail=detail,
    )
    log.info(
        "coverage_diff_persisted", run_id=run_id, coverage_pct=result.coverage_pct,
        missing=len(result.missing), thin=len(result.thin_clusters),
    )
    return result
