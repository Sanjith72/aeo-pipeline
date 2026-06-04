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
from ..reference.framework import load_framework
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
    run_id: int, *, topic: str | None = None, llm: LLMClient | None = None
) -> StoredBlueprint | None:
    """Generate (or reuse) the versioned blueprint and pin it to the run.
    Returns None when the generator is disabled."""
    cfg = get_settings().reference_architecture
    if not cfg.enabled:
        return None
    framework = load_framework()
    topic = topic or cfg.topic or framework.topic
    patterns = build_competitor_patterns(framework.required_entities)
    blueprint = generate_blueprint(
        topic=topic, framework=framework, patterns=patterns, llm=llm,
        engine_target=cfg.engine_target,
    )
    stored = blueprints_repo.save_versioned(blueprint)
    blueprints_repo.pin_run(run_id, stored.id)
    log.info(
        "blueprint_pinned", run_id=run_id, topic=topic, version=stored.blueprint.version,
        reused=stored.reused, generator=stored.blueprint.generator, nodes=len(stored.blueprint.sitemap),
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
) -> CoverageDiffResult:
    """Diff the discovered sitemap against the pinned blueprint and persist it."""
    reference = reference or load_reference()
    discovered = discovered_pages(scored, reference)
    result = coverage_diff(stored.blueprint, discovered)
    coverage_repo.put(
        run_id,
        blueprint_id=stored.id,
        target_id=target_id,
        coverage_pct=result.coverage_pct,
        missing_count=len(result.missing),
        thin_count=len(result.thin_clusters),
        detail=result.to_detail(),
    )
    log.info(
        "coverage_diff_persisted", run_id=run_id, coverage_pct=result.coverage_pct,
        missing=len(result.missing), thin=len(result.thin_clusters),
    )
    return result
