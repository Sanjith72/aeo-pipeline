"""
Orchestrator — owns a run across both pipeline phases.

Crawl/score phase (``run_urls``):
    start run → crawl (async, browser reused) → batch PageSpeed (async)
    → per page: persist → fingerprint short-circuit OR extract+score+persist
    → finish run

Analysis phase (``analyze_run``):
    per scored client page → Gap analysis → Validate (recommend + simulate +
    retry ≤3) → Report, each page isolated by the Error Sink. Run it after a
    scored crawl (``aeo analyze -r RUN``) to produce the per-page deliverable.

The fingerprint check happens *before* the page is upserted for this run so
it compares against prior runs only (otherwise it would always match the row
we just wrote). On an unchanged page the prior extraction + score are cloned
forward, skipping the expensive extract + LLM work.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..crawl import fingerprint
from ..crawl.discovery import discover
from ..crawl.prioritize import PageInput, load_prioritization_cfg, persist_ranking, prioritize
from ..crawl.prioritize import classify as classify_page_type
from ..crawl.runner import fetch_many
from ..extract import pagespeed
from ..logging import get_logger
from ..nlp.llm import LLMClient, get_client
from ..nlp.perplexity import get_perplexity_client
from ..obs import page_guard
from ..reference import load_reference
from ..reference.domain_config import load_domain_config
from ..scoring.rubric import load_rubric
from ..settings import get_settings
from ..storage.models import FetchedPage, PageScore, Target
from ..storage.repos import extractions as extractions_repo
from ..storage.repos import outcomes as outcomes_repo
from ..storage.repos import runs as runs_repo
from ..storage.repos import scores as scores_repo
from ..utils.url import normalize
from .analysis import analyze_page, build_competitor_pool, is_could_not_improve, is_improved
from .reference_arch import compute_and_persist_coverage, generate_and_pin_blueprint
from .stages import ExtractStage, PersistStage, ScoreStage

log = get_logger(__name__)

_PSI_MAX_CONCURRENCY = 5

# Progressive results (#7): the orchestrator reports a stage boundary to an optional
# sink — (stage_name, counts) — so a polling/streaming client can render findings as
# they arrive instead of waiting on one long spinner. Purely additive: the default
# None sink is a no-op and the pipeline logic is untouched.
ProgressFn = Callable[[str, dict[str, Any]], None]

# R2-2 drop-off safety: an optional predicate the run loop polls between pages. When it
# returns True the run early-exits (finishing as 'partial'), so a user who abandons
# mid-run doesn't burn the full top-N extract+score+LLM compute. None = never cancel.
CancelFn = Callable[[], bool]

# The major stages a run reports, in order. Exposed so a client (and the tests) can
# assert/scaffold against the contract without guessing stage names. ``profile`` is the
# R2-2 homepage-first partial: a structural profile surfaced immediately after discovery
# so the UI can render findings before the slow top-N crawl/analyze runs.
RUN_STAGES = ("discover", "profile", "blueprint", "coverage", "crawl", "analyze", "report")


def _emit(progress: ProgressFn | None, stage: str, **counts: Any) -> None:
    """Report a stage boundary to an optional progress sink. Best-effort and isolated:
    a broken sink is logged, never raised — progress reporting must not affect the run."""
    if progress is None:
        return
    try:
        progress(stage, counts)
    except Exception:  # a broken sink must never break the pipeline
        log.warning("progress_emit_failed", stage=stage)


@dataclass(slots=True)
class RunSummary:
    run_id: int
    run_key: str
    total: int = 0
    extracted: int = 0
    scored: int = 0
    unchanged: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int | str]:
        return {
            "run_id": self.run_id,
            "run_key": self.run_key,
            "total": self.total,
            "extracted": self.extracted,
            "scored": self.scored,
            "unchanged": self.unchanged,
            "failed": self.failed,
        }


@dataclass(slots=True)
class AnalysisSummary:
    run_id: int
    total: int = 0
    analyzed: int = 0
    improved: int = 0
    could_not_improve: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "run_id": self.run_id,
            "total": self.total,
            "analyzed": self.analyzed,
            "improved": self.improved,
            "could_not_improve": self.could_not_improve,
            "failed": self.failed,
        }


class Orchestrator:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or get_client()
        self.extract = ExtractStage()
        self.score = ScoreStage(self._llm)
        self.persist = PersistStage()

    async def run_urls(
        self,
        urls: Iterable[str],
        *,
        target: Target,
        label: str | None = None,
        do_score: bool = True,
    ) -> RunSummary:
        """Crawl + extract (+ score) an explicit URL list. ``do_score=False`` stops
        after extraction so scoring can run later as a separate phase (``aeo score``)."""
        run = runs_repo.start(label=label)
        return await self._run_pages(list(urls), run=run, target=target, do_score=do_score)

    async def run_site(
        self,
        domain: str,
        *,
        target: Target,
        label: str | None = None,
        do_score: bool = True,
        max_urls: int | None = None,
        progress: ProgressFn | None = None,
        force_recrawl: bool = False,
        should_cancel: CancelFn | None = None,
    ) -> RunSummary:
        """Site Discovery → Page Prioritization → crawl+extract(+score) the top-N.

        Discovers the domain's URL inventory (sitemap, then recursive), ranks every
        page by value, persists the *full* ranking for observability, and runs the
        per-page pipeline on only the ``selected`` top-N. This is the front of the
        Crawler block the v3 architecture specifies — Site Discovery → Page
        Prioritization → Page Crawler — as one call."""
        cfg = load_prioritization_cfg()
        # Per-domain onboarding (config/domains/{domain}.yaml): supplies defaults for
        # max_urls / label and (downstream) topic + engine_target for the blueprint.
        dc = load_domain_config(domain)
        if max_urls is None and dc is not None and dc.max_urls is not None:
            max_urls = dc.max_urls
        discovery = await discover(domain, max_urls=max_urls)
        scored = prioritize(
            [PageInput(d.url, d.internal_links) for d in discovery.urls], cfg
        )

        run = runs_repo.start(label=label or (dc.label if dc else None) or f"site:{domain}")
        persist_ranking(run.id, scored)
        selected = [s.url for s in scored if s.selected]
        log.info(
            "site_discovered", run_key=run.run_key, domain=domain,
            source=discovery.source, discovered=len(scored), selected=len(selected),
        )
        _emit(progress, "discover", discovered=len(scored), selected=len(selected),
              source=discovery.source)

        # R2-2 homepage-first incremental: surface a structural profile immediately —
        # the same industry/headline/business-model the fast /api/profile returns — so
        # the UI can render findings before the slow top-N crawl/analyze that follows.
        # Best-effort and isolated (mirrors the blueprint block) and only built when a
        # sink is listening, so the no-progress paths (CLI, tests) pay nothing.
        if progress is not None:
            try:
                from ..intelligence import build_site_profile

                prof = build_site_profile(
                    domain=domain, discovered=scored,
                    topic=(dc.topic if dc else None), llm=self._llm,
                )
                _emit(
                    progress, "profile",
                    industry=prof.industry, headline=prof.headline(),  # headline() is a method
                    business_model=prof.business_intent.model.value,
                    scenario=prof.strategy.scenario.value, pages=len(scored),
                )
            except Exception as exc:  # a partial preview must never abort the run
                log.warning("homepage_profile_skipped", run_key=run.run_key, error=str(exc))

        # v4 Reference Architecture: generate+pin the versioned blueprint and run
        # the site-level Coverage Diff (over the full discovered inventory, not just
        # the crawled top-N). Best-effort and isolated — a generator/DB hiccup logs
        # and is skipped so it never aborts the crawl that follows.
        try:
            stored_bp = generate_and_pin_blueprint(run.id, domain=domain, llm=self._llm)
            if stored_bp is not None:
                _emit(progress, "blueprint", version=getattr(stored_bp.blueprint, "version", None))
                compute_and_persist_coverage(
                    run.id, stored_bp, scored, target_id=target.id,
                    reference=load_reference(), domain=domain, llm=self._llm,
                    # Per-domain onboarding topic only (not the seeded blueprint topic) so
                    # the site report's industry reflects *this* site, not the PEV seed.
                    topic=(dc.topic if dc else None),
                )
                _emit(progress, "coverage", nodes=len(getattr(stored_bp.blueprint, "sitemap", []) or []))
        except Exception as exc:
            log.warning("reference_architecture_skipped", run_key=run.run_key, error=str(exc))

        if not selected:  # discovery found nothing crawlable — close the run cleanly
            runs_repo.finish(run.id, status="succeeded")
            _emit(progress, "crawl", total=0, scored=0, unchanged=0, failed=0)
            return RunSummary(run_id=run.id, run_key=run.run_key)

        return await self._run_pages(
            selected, run=run, target=target, do_score=do_score, progress=progress,
            force_recrawl=force_recrawl, should_cancel=should_cancel,
        )

    async def _run_pages(
        self,
        urls: list[str],
        *,
        run,
        target: Target,
        do_score: bool,
        progress: ProgressFn | None = None,
        force_recrawl: bool = False,
        should_cancel: CancelFn | None = None,
    ) -> RunSummary:
        """Crawl → extract → (score) every URL into ``run``, isolated per page.
        Shared by ``run_urls`` (explicit list) and ``run_site`` (prioritized top-N).

        ``force_recrawl`` bypasses the fingerprint skip gate so an explicit re-crawl
        re-reads unchanged pages. ``should_cancel`` is polled before each page so an
        abandoned run early-exits (status ``partial``) instead of burning the full
        top-N extract+score+LLM compute (R2-2 drop-off safety)."""
        client_id, competitor_id = _owner_ids(target)
        summary = RunSummary(run_id=run.id, run_key=run.run_key)
        log.info("run_start", run_key=run.run_key, target=target.name,
                 do_score=do_score, urls=len(urls), force_recrawl=force_recrawl)

        def _cancelled() -> bool:
            return should_cancel is not None and bool(should_cancel())

        try:
            pages = await fetch_many(urls)
            summary.total = len(pages)

            # PageSpeed only matters for the load_speed score; skip it otherwise.
            psi_map: dict[str, dict] = {}
            if do_score and not _cancelled():
                psi_map = await self._psi_batch([p.url for p in pages if p.success])

            cancelled = False
            for page in pages:
                # Drop-off safety: stop before the expensive per-page work if the run
                # was abandoned. Pages already processed stay; the run is 'partial'.
                if _cancelled():
                    cancelled = True
                    log.info("run_cancelled", run_key=run.run_key,
                             processed=summary.extracted, total=summary.total)
                    break
                self._process_one(page, run.id, client_id, competitor_id, psi_map,
                                  summary, do_score, force_recrawl=force_recrawl)

            status = "partial" if (cancelled or summary.failed) else "succeeded"
            runs_repo.finish(run.id, status=status,
                             notes="cancelled mid-run" if cancelled else None)
            log.info("run_complete", run_key=run.run_key, cancelled=cancelled,
                     **_count_fields(summary))
            _emit(progress, "crawl", total=summary.total, scored=summary.scored,
                  unchanged=summary.unchanged, failed=summary.failed, cancelled=cancelled)
        except Exception as exc:  # record the failure on the run row
            runs_repo.finish(run.id, status="failed", notes=str(exc))
            log.error("run_failed", run_key=run.run_key, error=str(exc))
            raise

        return summary

    def score_run(self, run_id: int, limit: int = 100_000) -> int:
        """Score every extracted-but-unscored page in a run. Powers the
        standalone ``aeo score`` phase and re-scoring after a crawl-only run."""
        pending = scores_repo.pages_pending_score(run_id, limit=limit)
        scored = 0
        for page_id in pending:
            bundle = extractions_repo.get(page_id)
            if bundle is None:
                continue
            page_score = self.score.run(bundle, run_id)
            self.persist.score(page_score, scored_by=_scored_by(page_score))
            scored += 1
        log.info("score_run_complete", run_id=run_id, scored=scored)
        return scored

    def analyze_run(
        self, run_id: int, *, persist: bool = True, limit: int = 100_000,
        progress: ProgressFn | None = None,
    ) -> AnalysisSummary:
        """Run the back half of the pipeline (Gap -> Validate -> Independent-Validate
        -> Report) for every scored client page in a run. Each page is isolated by
        the Error Sink, so one failure is recorded and skipped without aborting the
        run. Pages fan out across a thread pool when
        ``AEO__VALIDATION__ANALYSIS_CONCURRENCY > 1`` (the v4 Parallel Processor at
        the analysis tier) — output is order-independent, so the tally is the same."""
        reference = load_reference()
        rubric = load_rubric()
        cfg = load_prioritization_cfg()
        pool = build_competitor_pool(scores_repo.latest_competitor_scores(), reference)

        settings = get_settings()
        perplexity = get_perplexity_client()
        independent = settings.validation.independent_enabled
        adversarial = settings.validation.adversarial_enabled
        verify_citations = settings.validation.verify_citations
        concurrency = max(1, settings.validation.analysis_concurrency)

        summary = AnalysisSummary(run_id=run_id)
        pages = scores_repo.scored_pages_for_run(run_id, owner="client", limit=limit)
        summary.total = len(pages)
        log.info(
            "analyze_run_start", run_id=run_id, pages=len(pages), competitors=len(pool),
            independent=independent, adversarial=adversarial, concurrency=concurrency,
        )

        def work(row: dict) -> dict[str, bool]:
            return self._analyze_one(
                row, run_id=run_id, reference=reference, rubric=rubric, cfg=cfg,
                pool=pool, perplexity=perplexity, independent=independent,
                adversarial=adversarial, verify_citations=verify_citations, persist=persist,
            )

        if concurrency > 1 and len(pages) > 1:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="analysis") as ex:
                results = list(ex.map(work, pages))
        else:
            results = [work(row) for row in pages]

        for r in results:
            summary.analyzed += int(r["analyzed"])
            summary.improved += int(r["improved"])
            summary.could_not_improve += int(r["cni"])
            summary.failed += int(r["failed"])

        log.info("analyze_run_complete", **summary.as_dict())
        _emit(progress, "analyze", total=summary.total, analyzed=summary.analyzed,
              improved=summary.improved, could_not_improve=summary.could_not_improve,
              failed=summary.failed)
        return summary

    def _analyze_one(
        self, row: dict, *, run_id: int, reference, rubric, cfg, pool, perplexity,
        independent: bool, adversarial: bool = False, verify_citations: bool = False, persist: bool,
    ) -> dict[str, bool]:
        """Analyze a single scored page, isolated by the Error Sink. Returns the
        per-page tally contribution (so the loop can run sequentially or pooled)."""
        page_id, url = row["page_id"], row["url"]
        out = {"analyzed": False, "improved": False, "cni": False, "failed": False}
        with page_guard("analysis", run_id=run_id, page_id=page_id) as outcome:
            bundle = extractions_repo.get(page_id)
            if bundle is None:
                return out
            page_score = self.score.run(bundle, run_id)
            result = analyze_page(
                bundle=bundle,
                score=page_score,
                url=url,
                reference=reference,
                rubric=rubric,
                llm=self._llm,
                competitors=pool,
                page_type=classify_page_type(url, cfg),
                persist=persist,
                perplexity=perplexity,
                independent=independent,
                adversarial=adversarial,
                verify_citations=verify_citations,
            )
            out["analyzed"] = True
            out["improved"] = is_improved(result)
            out["cni"] = is_could_not_improve(result)
        if outcome.failed:
            out["failed"] = True
        return out

    async def audit_cycle(
        self,
        domain: str,
        *,
        target: Target,
        label: str | None = None,
        max_urls: int | None = None,
        progress: ProgressFn | None = None,
        force_recrawl: bool = False,
        should_cancel: CancelFn | None = None,
    ) -> dict:
        """The v4 Weekly Audit Loop entrypoint: Site Discovery → Page Prioritization
        → blueprint (generate+pin) → Coverage Diff → crawl/score (content-hash
        gated, unchanged pages carried forward) → analyze (Gap → Validate →
        Independent-Validate → per-page report) → site-level report. Designed to be
        invoked weekly by a systemd timer / cron (see ops/).

        ``progress`` (optional) receives a (stage, counts) update at each stage boundary
        (see ``RUN_STAGES``) so a polling/streaming client can render incrementally."""
        run_summary = await self.run_site(
            domain, target=target, label=label or f"audit:{domain}", do_score=True,
            max_urls=max_urls, progress=progress,
            force_recrawl=force_recrawl, should_cancel=should_cancel,
        )
        analysis = self.analyze_run(run_summary.run_id, progress=progress)
        site_report_id = self._build_and_persist_site_report(run_summary.run_id, target, domain=domain)
        milestones = await self._verify_milestones(run_summary.run_id, target, domain=domain)
        log.info(
            "audit_cycle_complete", run_id=run_summary.run_id, domain=domain,
            site_report_id=site_report_id, milestones_verified=milestones.get("newly_verified", 0),
        )
        _emit(progress, "report", site_report_id=site_report_id)
        return {
            "run": run_summary.as_dict(),
            "analysis": analysis.as_dict(),
            "site_report_id": site_report_id,
            "milestones": milestones,
        }

    async def _verify_milestones(self, run_id: int, target: Target, *, domain: str) -> dict:
        """Auto-verify the client's implementation milestones against the freshly-crawled
        site (the weekly 'did they do it?' check). Best-effort and isolated — like
        completion detection, this is bookkeeping on top of the audit and must never abort
        it. Only clients are tracked; competitors and a missing milestone plan no-op."""
        if target.kind != "client":
            return {"checked": 0, "newly_verified": 0, "verified_keys": []}
        try:
            from ..storage.repos import milestones as milestones_repo
            from ..storage.repos import pages as pages_repo

            if not milestones_repo.has_milestones(target.id):
                return {"checked": 0, "newly_verified": 0, "verified_keys": []}
            # Feed the verifier the run's full crawled inventory so deep pages (not linked
            # from the homepage) still count as live.
            slugs = [p["url_normalized"] for p in pages_repo.by_run(run_id) if p.get("url_normalized")]
            from .milestone_audit import verify_client_milestones

            return await verify_client_milestones(
                target.id, domain, run_id=run_id, discovered_slugs=slugs,
            )
        except Exception as exc:  # verification must never break the audit cycle
            log.warning("milestone_verify_skipped", run_id=run_id, domain=domain, error=str(exc))
            return {"checked": 0, "newly_verified": 0, "verified_keys": [], "error": str(exc)}

    async def dry_run(
        self,
        domain: str,
        *,
        max_urls: int | None = None,
        pages: int = 5,
        use_llm: bool = False,
        draft_samples: bool = True,
    ) -> dict:
        """In-memory preview — discover → blueprint → site coverage diff [→ score top
        pages], writing NOTHING to the database. The demo/onboarding path (ported
        idea): show what an audit *would* surface for a domain without a DB, a run
        row, or any persistence. Network is still used (discovery + optional crawl);
        only persistence is skipped.

        ``use_llm=False`` (default) keeps it fast + offline-friendly with a
        deterministic blueprint. ``pages`` caps how many top URLs are crawled+scored
        in memory (0 = structural preview only). ``draft_samples=False`` skips drafting
        sample copy for the top missing pages — a single full-prose draft on a local model
        is multi-second, so the fast ``/api/profile`` intake (which never renders these
        drafts) turns it off to stay in the seconds-not-minutes range."""
        from ..processor.coverage_diff import coverage_diff
        from ..recommender.draft import draft_missing_page
        from ..reference.competitor_patterns import CompetitorPatterns
        from ..reference.domain_config import load_domain_config
        from ..reference.framework_bootstrap import resolve_framework
        from ..reference.generator import generate_blueprint
        from .reference_arch import discovered_pages

        settings = get_settings()
        cfg = load_prioritization_cfg()
        dc = load_domain_config(domain)
        if max_urls is None and dc is not None and dc.max_urls is not None:
            max_urls = dc.max_urls

        discovery = await discover(domain, max_urls=max_urls)
        scored = prioritize([PageInput(d.url, d.internal_links) for d in discovery.urls], cfg)
        selected = [s for s in scored if s.selected]

        # Blueprint (in-memory, not persisted). Deterministic by default; competitor
        # patterns are empty (dry-run never reads the DB).
        ra = settings.reference_architecture
        # Per-site framework: curated per-domain file if present, else a bootstrapped
        # generic/LLM-tailored skeleton — never the shared cybersecurity seed for an
        # unconfigured site (that seed leaked cyber recs onto every domain).
        framework = resolve_framework(
            domain, llm=(self._llm if use_llm else None), topic=(dc.topic if dc else None)
        )
        topic = (dc.topic if dc else None) or framework.topic or ra.topic
        # The blueprint legitimately falls back to the seeded framework topic (the
        # generator is built for it), but that global seed must NOT become this site's
        # industry. Only a per-domain onboarding topic identifies the *site*; otherwise
        # leave it None so infer_industry falls back to the business-model coarse label.
        intake_topic = dc.topic if (dc and dc.topic) else None
        engine_target = (dc.engine_target if dc and dc.engine_target else None) or ra.engine_target
        blueprint = generate_blueprint(
            topic=topic, framework=framework, patterns=CompetitorPatterns(),
            llm=(self._llm if use_llm else None), engine_target=engine_target,
        )

        # Site-level coverage diff (pure).
        reference = load_reference()
        cov = coverage_diff(blueprint, discovered_pages(scored, reference))

        # SP-1 intelligence layer: classify the site, infer the business model, find
        # journey gaps, and route to a scenario + prioritized strategy — all in memory.
        from ..intelligence import build_site_profile

        try:
            profile = build_site_profile(
                domain=domain, discovered=scored, coverage=cov,
                topic=intake_topic, llm=(self._llm if use_llm else None),
            )
        except Exception as exc:  # best-effort, mirrors the persisted path's isolation
            log.warning("site_profile_skipped", domain=domain, error=str(exc))
            profile = None

        # Sample the new content-drafting on the top missing pages (deterministic
        # scaffold by default; full prose when --llm). Bounded so the preview stays fast.
        from ..validation.draft_check import validate_page_draft

        top_missing: list[dict] = []
        for i, m in enumerate(cov.missing_by_priority()[:10]):
            entry = {"slug": m.slug, "priority": m.priority, "page_type": m.page_type, "title": m.title}
            if draft_samples and i < 3:
                payload = draft_missing_page(
                    m, topic=topic, llm=(self._llm if use_llm else None),
                    reference=reference, origin=domain,
                ).to_payload()
                # Same Block-4 gate as the persisted site report: no LLM-authored
                # page reaches the preview without the independent + citation check.
                payload["validation"] = validate_page_draft(payload, url=domain)
                entry["draft"] = payload
            top_missing.append(entry)

        # Optional per-page scoring of the top-N, in memory (no persist).
        page_scores: list[dict] = []
        crawl_urls = [s.url for s in selected][: max(0, pages)]
        if crawl_urls:
            fetched = await fetch_many(crawl_urls)
            for page in fetched:
                if not page.success:
                    page_scores.append({"url": page.url, "error": page.error or "fetch failed"})
                    continue
                bundle = self.extract.run(page, 0, None)  # page_id=0 — not persisted
                ps = self.score.run(bundle, 0)            # run_id=0 — not persisted
                page_scores.append({
                    "url": page.url, "total": ps.total, "max_possible": ps.max_possible,
                    "priority_tier": ps.priority_tier,
                })

        log.info(
            "dry_run_complete", domain=domain, discovered=len(scored), selected=len(selected),
            topic=topic, engine_target=engine_target, coverage_pct=cov.coverage_pct,
            scored_pages=len(page_scores),
            scenario=profile.strategy.scenario.value if profile else None,
            business_model=profile.business_intent.model.value if profile else None,
        )
        return {
            "mode": "dry-run",
            "domain": domain,
            "source": discovery.source,
            "discovered": len(scored),
            "selected": len(selected),
            # v5: the top of the prioritized ranking, so callers (the free overview's
            # pack builder) can group pages without re-running discovery. Bounded; the
            # full ranking only ever persists on real runs (persist_ranking). The homepage
            # is force-included even when it ranks past the slice — the pack builder's
            # "homepage always in Pack 1" rule needs the entry to be present to apply.
            "ranking": _ranking_slice(scored, limit=60),
            "topic": topic,
            "engine_target": engine_target,
            "profile": profile.to_dict() if profile else None,
            "blueprint": {
                "version": blueprint.version,
                "generator": blueprint.generator,
                "nodes": len(blueprint.sitemap),
                "config_fingerprint": blueprint.config_fingerprint,
            },
            "coverage": {
                "pct": cov.coverage_pct,
                "matched": cov.matched_count,
                "total_nodes": cov.total_nodes,
                "missing": len(cov.missing),
                "thin_clusters": len(cov.thin_clusters),
                "top_missing": top_missing,
            },
            "pages": page_scores,
            "db_writes": 0,
        }

    def _build_and_persist_site_report(
        self, run_id: int, target: Target, *, domain: str | None = None
    ) -> int | None:
        """Assemble + persist the site-level report from the run's coverage diff,
        pinned blueprint, and per-page reports. Returns the report id, or None when
        no blueprint/coverage was produced for the run. When content drafting is enabled
        (``reference_architecture.draft_missing_pages``), the top missing pages are
        drafted into ready-to-publish copy + JSON-LD on the site report."""
        from ..processor.coverage_diff import CoverageDiffResult
        from ..report import build_site_report
        from ..storage.repos import blueprints as blueprints_repo
        from ..storage.repos import coverage as coverage_repo
        from ..storage.repos import reports as reports_repo
        from ..storage.repos import site_reports as site_reports_repo

        cov_row = coverage_repo.get(run_id)
        if not cov_row or not cov_row.get("blueprint_id"):
            return None
        stored = blueprints_repo.get(cov_row["blueprint_id"])
        if stored is None:
            return None

        detail = cov_row.get("detail") or {}
        coverage = CoverageDiffResult.from_detail(detail)
        site_profile = detail.get("site_profile")  # SP-1 strategy, embedded at coverage time
        pages = []
        for rep in reports_repo.for_run(run_id):
            overview = (rep.get("sections") or {}).get("overview", {}) or {}
            pages.append(
                {
                    "url": overview.get("url"),
                    "total": overview.get("total"),
                    "max_possible": overview.get("max_possible"),
                    "priority_tier": overview.get("priority_tier"),
                    "review_status": rep.get("review_status"),
                }
            )

        ra = get_settings().reference_architecture
        draft_limit = ra.draft_limit if ra.draft_missing_pages else 0
        site = build_site_report(
            blueprint=stored.blueprint, coverage=coverage, pages=pages,
            run_id=run_id, target_id=target.id, blueprint_id=stored.id,
            llm=self._llm, origin=domain, draft_limit=draft_limit,
            site_profile=site_profile,
        )
        return site_reports_repo.put(site)

    def _persist_skill_scores(self, page_score: PageScore, bundle, run_id: int) -> None:
        """v5 CH-04: derive + persist the five-skill layer for a freshly-scored page.
        Best-effort and isolated — like completion detection, this is bookkeeping on top
        of the scored crawl and must never abort it. ``skill_llm`` off (or a disabled LLM)
        keeps Messaging/Conversion deterministic; the mapped skills are always free."""
        settings = get_settings()
        if not settings.scoring.skills_enabled:
            return
        try:
            from ..scoring.skills import build_skill_scores
            from ..storage.repos import skill_scores as skill_scores_repo

            llm = self._llm if settings.scoring.skill_llm else None
            payload = build_skill_scores(page_score, bundle, llm=llm)
            skill_scores_repo.put(page_score.page_id, run_id, payload)
        except Exception as exc:  # skill scoring is additive — never fatal to a run
            log.warning(
                "skill_scores_skipped",
                page_id=getattr(page_score, "page_id", None), error=str(exc),
            )

    def _detect_completions(self, page: FetchedPage, run_id: int, page_score: PageScore) -> None:
        """Retention Engine bookkeeping: reconcile this URL's pending recommendation
        outcomes against the freshly RE-SCORED page — an outcome flips to ``implemented``
        only when its targeted criterion's tier actually rose (criterion-honest), not on
        a bare content-hash change. Best-effort and isolated — completion detection must
        never abort the crawl that carries it."""
        if not get_settings().retention.enabled:
            return
        try:
            new_tiers = {name: c.value for name, c in page_score.criteria.items()}
            flipped = outcomes_repo.mark_from_recrawl(
                page.url_normalized, run_id, page.content_hash, new_tiers
            )
            if flipped:
                log.info(
                    "recommendations_implemented",
                    url=page.url, run_id=run_id, count=flipped,
                )
        except Exception as exc:  # detection is bookkeeping, never fatal to a run
            log.warning("completion_detection_skipped", url=page.url, error=str(exc))

    def _process_one(
        self,
        page: FetchedPage,
        run_id: int,
        client_id: int | None,
        competitor_id: int | None,
        psi_map: dict[str, dict],
        summary: RunSummary,
        do_score: bool,
        force_recrawl: bool = False,
    ) -> None:
        if not page.success:
            self.persist.page(page, run_id, client_id, competitor_id)
            summary.failed += 1
            return

        # Fingerprint short-circuit only pays off when scoring (it skips the
        # LLM). For crawl-only runs, extraction is cheap — just redo it. An explicit
        # re-crawl (force_recrawl) bypasses the skip gate entirely (R2-2).
        if do_score and not force_recrawl and fingerprint.should_skip(page.url_normalized, page.content_hash):
            stored = self.persist.page(page, run_id, client_id, competitor_id)
            if self.persist.copy_unchanged(page.url_normalized, stored.id, run_id):
                summary.unchanged += 1
                log.info("unchanged_skip", url=page.url)
                return
        else:
            stored = self.persist.page(page, run_id, client_id, competitor_id)

        psi = psi_map.get(page.url_normalized) if do_score else None
        bundle = self.extract.run(page, stored.id, psi)
        self.persist.extraction(bundle)
        summary.extracted += 1

        # Opt-in pgvector indexing for the agent's semantic_search (embed_index.py).
        # Best-effort by contract — a failure can never touch the audit.
        from .embed_index import maybe_index_page

        maybe_index_page(page, stored.id, bundle)

        if do_score:
            page_score = self.score.run(bundle, run_id)
            self.persist.score(page_score, scored_by=_scored_by(page_score))
            summary.scored += 1
            # v5 CH-04: the five-skill derived layer, persisted alongside the rubric score.
            # Additive + isolated — a skills failure must never abort the crawl that carries
            # it (mirrors _detect_completions).
            self._persist_skill_scores(page_score, bundle, run_id)
            # Retention Engine (#11): verify recommendation completions AFTER re-scoring,
            # so the check is criterion-honest (did the targeted criterion's tier rise?)
            # rather than a self-grading hash-only signal. A changed page always re-scores
            # (a changed hash never fingerprint-skips), so the completion is never missed;
            # an unchanged page can't have a pending outcome that should flip.
            self._detect_completions(page, run_id, page_score)

    async def _psi_batch(self, urls: list[str]) -> dict[str, dict]:
        """Fetch PageSpeed for many URLs concurrently. Empty when no API key —
        the load_speed scorer then falls back to a neutral score."""
        s = get_settings()
        if not s.psi_api_key:
            return {}

        sem = asyncio.Semaphore(min(_PSI_MAX_CONCURRENCY, max(1, s.crawler.concurrency)))

        async def _one(u: str) -> tuple[str, dict | None]:
            async with sem:
                return normalize(u), await pagespeed.fetch(u)

        pairs = await asyncio.gather(*[_one(u) for u in urls])
        return {key: data for key, data in pairs if data is not None}


def _ranking_slice(scored: list, limit: int = 60) -> list[dict[str, Any]]:
    """The top-``limit`` of the prioritized ranking as plain dicts, with the homepage
    always present (appended if it ranked past the cut) so downstream pack construction
    can honor the 'homepage in Pack 1' rule (v5 CH-03)."""
    top = list(scored[:limit])
    if not any(s.page_type == "homepage" for s in top):
        home = next((s for s in scored if s.page_type == "homepage"), None)
        if home is not None:
            top.append(home)
    return [
        {
            "url": s.url,
            "page_type": s.page_type,
            "final_score": s.final_score,
            "rank": s.rank,
            "selected": s.selected,
        }
        for s in top
    ]


def _owner_ids(target: Target) -> tuple[int | None, int | None]:
    """crawled_pages enforces exactly one owner (chk_single_owner)."""
    if target.kind == "client":
        return target.id, None
    return None, target.id


def _scored_by(page_score: PageScore) -> str:
    kinds = {c.scored_by for c in page_score.criteria.values()}
    if "hybrid" in kinds:
        return "hybrid"
    if kinds == {"deterministic"}:
        return "deterministic"
    return "+".join(sorted(kinds))


def _count_fields(summary: RunSummary) -> dict[str, int]:
    return {
        "total": summary.total,
        "scored": summary.scored,
        "unchanged": summary.unchanged,
        "failed": summary.failed,
    }
