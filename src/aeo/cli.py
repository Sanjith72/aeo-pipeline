"""
Command-line interface (``aeo …``).

Thin layer over the pipeline + repos — every command bootstraps logging, then
delegates. Commands:

    aeo migrate                 apply pending DB migrations
    aeo targets                 list configured clients & competitors
    aeo onboard NAME DOMAIN     name + domain in -> framework + entities.yaml + competitors out
    aeo audit  DOMAIN -t NAME   discover → prioritize → crawl → extract → score
    aeo discover DOMAIN         discover + rank a site's URLs (no crawl, no DB)
    aeo profile  DOMAIN         classify site + AEO strategy/action plan (no crawl-score, no DB)
    aeo plan     NAME           AEO blueprint + build plan from a brief — NO website required
    aeo deliverables -r RUN_ID  developer-ready asset bundle (sitemap.xml, page specs, briefs)
    aeo serve                   run the HTTP API (FastAPI) the guided UI calls  [api extra]
    aeo run    URLS… -t NAME    crawl → extract → score (the full pipeline)
    aeo crawl  URLS… -t NAME    crawl → extract only (score later)
    aeo score  -r RUN_ID        score a run's extracted-but-unscored pages
    aeo analyze -r RUN_ID       gap → recommend → validate → report a scored run
    aeo enqueue URLS… -t NAME   queue a crawl batch for a worker
    aeo worker                  drain the job queue
    aeo status [-r RUN_ID]      DB health, queue depth, run report
    aeo trace  PAGE_ID          dump a page's agent journey (observability)
    aeo report TARGET           render the per-page AEO/SEO reports (deliverable)

  v4 Reference Architecture:
    aeo audit-cycle DOMAIN -t NAME   weekly loop: blueprint -> coverage -> crawl -> analyze -> site report
    aeo blueprint generate           generate (or reuse) the versioned ideal-site blueprint
    aeo blueprint show               print a stored blueprint
    aeo coverage -r RUN_ID           site-level Coverage Diff (missing/thin pages)
    aeo site-report -r RUN_ID        render the site-level AEO report
    aeo refinements [--propose]      validated-wins criteria-target proposals (human-gated)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import typer

from .logging import configure, get_logger
from .pipeline import Orchestrator, Worker, enqueue_batch
from .report import render_report
from .storage.db import health_check
from .storage.migrate import apply_pending
from .storage.models import Target
from .storage.repos import jobs as jobs_repo
from .storage.repos import reports as reports_repo
from .storage.repos import scores as scores_repo
from .storage.repos import targets as targets_repo
from .storage.repos import traces as traces_repo

# Help/report text uses Unicode (→, ≤, …). On a Windows cp1252 console, rendering it
# raises UnicodeEncodeError, so force UTF-8 on the streams at import — covers both the
# ``aeo`` entry point (calls ``app`` directly) and ``python -m aeo.cli`` (calls ``main``).
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):  # pragma: no cover - non-reconfigurable / non-Windows
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

app = typer.Typer(add_completion=False, help="AEO content crawler & rubric scorer.")
log = get_logger(__name__)


def _bootstrap() -> None:
    configure()


def _collect_urls(urls: list[str] | None, file: Path | None) -> list[str]:
    out = list(urls or [])
    if file:
        text = Path(file).read_text(encoding="utf-8")
        out += [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    # Preserve order, drop dupes.
    seen: set[str] = set()
    deduped = [u for u in out if not (u in seen or seen.add(u))]
    if not deduped:
        raise typer.BadParameter("no URLs provided — pass URLs as arguments or --file")
    return deduped


def _resolve_target(name: str) -> Target:
    target = targets_repo.find(name)
    if target is None:
        typer.secho(f"unknown target {name!r} — run `aeo targets` to list them", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return target


def _print(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.command()
def migrate() -> None:
    """Apply pending database migrations."""
    _bootstrap()
    applied = apply_pending()
    typer.echo("applied: " + ", ".join(applied) if applied else "migrations up to date")


@app.command()
def targets() -> None:
    """List configured clients and competitors."""
    _bootstrap()
    for kind in ("client", "competitor"):
        for t in targets_repo.list_all(kind):  # type: ignore[arg-type]
            typer.echo(f"{kind:11} {t.name:18} {t.domain}")


@app.command(name="add-target")
def add_target(
    name: str = typer.Argument(..., help="Display name for the target, e.g. Acme"),
    domain: str = typer.Argument(..., help="Domain or URL, e.g. acme.com or https://acme.com"),
    competitor: bool = typer.Option(False, "--competitor", help="Register as a competitor (default: client)"),
    url: str | None = typer.Option(None, "--url", help="Full website URL (default https://<domain>)"),
) -> None:
    """Register a client (or --competitor) so `aeo audit` / `audit-cycle` can target it.
    Idempotent: re-running updates the domain/url. This is the one-time onboarding step
    for auditing a brand-new website."""
    _bootstrap()
    kind = "competitor" if competitor else "client"
    tgt = targets_repo.upsert(name, domain, kind, website_url=url)
    typer.echo(f"{kind} target ready: {tgt.name} ({tgt.domain}) id={tgt.id}")
    typer.echo(f"next: aeo audit-cycle {tgt.domain} -t {tgt.name}")


@app.command()
def onboard(
    name: str = typer.Argument(..., help="Display name for the client, e.g. Acme"),
    domain: str = typer.Argument(..., help="Domain or URL, e.g. acme.com or https://acme.com"),
    competitors: int = typer.Option(5, "--competitors", "-c", help="How many competitors to discover (capped at 12)"),
    engine_target: str | None = typer.Option(
        None, "--engine-target", help="perplexity | chatgpt_search | gemini | generic (default: generic)"
    ),
    category: str | None = typer.Option(
        None, "--category", help="Industry vertical for the Taxonomic Ceiling, e.g. 'healthcare', 'personal finance'"
    ),
    topic: str | None = typer.Option(None, "--topic", help="Topic hint (default: derived by the framework bootstrap)"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Use the LLM for framework tailoring + competitor discovery"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing per-domain onboarding YAML if present"),
) -> None:
    """One command from a bare client name + domain to a fully wired audit target.

    Chains what used to be three hand-authored steps: generates the per-domain
    ideal-site framework (``aeo framework bootstrap``), writes the per-run onboarding
    YAML (the file you'd otherwise copy from ``securin.io.yaml``), discovers and
    LIVE-VERIFIES real competitors via the LLM + a reachability probe (hallucinated
    domains are dropped, never written), merges the client + verified competitors into
    ``entities.yaml``, and registers every target. Idempotent — safe to re-run; existing
    files and entity entries are left alone, not clobbered. Review the generated/updated
    files, then `aeo audit-cycle`."""
    _bootstrap()
    from .reference.onboard import onboard_client

    result = onboard_client(
        name, domain, topic=topic, category=category, engine_target=engine_target,
        competitor_count=competitors, use_llm=use_llm, overwrite_domain_config=overwrite,
    )
    host = result.client.domain
    typer.echo(f"client target ready: {result.client.name} ({host}) id={result.client.id}")
    typer.echo(f"  framework   {result.framework_path}  [{result.framework_source}]")
    cfg_note = "" if result.domain_config_written else "  (already existed — left as-is; use --overwrite to replace)"
    typer.echo(f"  domain cfg  {result.domain_config_path}{cfg_note}")

    d = result.discovery
    typer.echo(f"  competitors proposed={d.raw_count}  verified={len(d.verified)}  dropped={len(d.dropped)}")
    for c in d.verified:
        typer.echo(f"    + {c.name:24} {c.domain}")
    for c in d.dropped:
        typer.secho(f"    x {c.name:24} {c.domain}   domain didn't resolve — check by hand", fg=typer.colors.YELLOW)

    e = result.entities
    typer.echo(f"  entities    {result.entities_path}  added={len(e.added)}  skipped(already present)={len(e.skipped)}")
    typer.echo(f"next: review the files above, then  aeo audit-cycle {host} -t {name}")


@app.command()
def run(
    urls: list[str] | None = typer.Argument(None, help="URLs to crawl"),
    file: Path | None = typer.Option(None, "--file", "-f", help="File of newline-separated URLs"),
    target: str = typer.Option("Securin", "--target", "-t", help="Client/competitor name"),
    label: str | None = typer.Option(None, "--label", "-l", help="Run label"),
    score: bool = typer.Option(True, "--score/--no-score", help="Score after extraction"),
) -> None:
    """Crawl, extract, and score a set of URLs."""
    _bootstrap()
    url_list = _collect_urls(urls, file)
    tgt = _resolve_target(target)
    summary = asyncio.run(Orchestrator().run_urls(url_list, target=tgt, label=label, do_score=score))
    _print(summary.as_dict())


@app.command()
def crawl(
    urls: list[str] | None = typer.Argument(None, help="URLs to crawl"),
    file: Path | None = typer.Option(None, "--file", "-f"),
    target: str = typer.Option("Securin", "--target", "-t"),
    label: str | None = typer.Option(None, "--label", "-l"),
) -> None:
    """Crawl + extract only (no scoring). Score later with `aeo score -r RUN_ID`."""
    _bootstrap()
    url_list = _collect_urls(urls, file)
    tgt = _resolve_target(target)
    summary = asyncio.run(Orchestrator().run_urls(url_list, target=tgt, label=label, do_score=False))
    _print(summary.as_dict())


@app.command()
def audit(
    domain: str = typer.Argument(..., help="Site domain or URL to audit end-to-end"),
    target: str = typer.Option("Securin", "--target", "-t", help="Client/competitor name"),
    label: str | None = typer.Option(None, "--label", "-l", help="Run label"),
    max_urls: int | None = typer.Option(None, "--max-urls", help="Cap discovery before ranking"),
    score: bool = typer.Option(True, "--score/--no-score", help="Score after extraction"),
    analyze: bool = typer.Option(False, "--analyze", help="Run Gap→Validate→Report after scoring"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="In-memory preview (discover→blueprint→coverage[+score]); writes NOTHING to the DB"
    ),
    pages: int = typer.Option(5, "--pages", help="[dry-run] crawl+score this many top pages in memory (0 = structural only)"),
    use_llm: bool = typer.Option(False, "--llm/--no-llm", help="[dry-run] use the LLM for blueprint synthesis"),
) -> None:
    """Site Discovery → Page Prioritization (top-N) → crawl → extract → score
    [→ analyze]. The single-command path from a bare domain to per-page reports.

    ``--dry-run`` previews what an audit would surface (discovered pages, the ideal-
    site blueprint, and the site-level coverage gap) entirely in memory, with no DB —
    ideal for onboarding a new domain or a stakeholder demo."""
    _bootstrap()
    orch = Orchestrator()
    if dry_run:
        result = asyncio.run(orch.dry_run(domain, max_urls=max_urls, pages=pages, use_llm=use_llm))
        _print(result)
        return
    tgt = _resolve_target(target)
    summary = asyncio.run(
        orch.run_site(domain, target=tgt, label=label, do_score=score, max_urls=max_urls)
    )
    _print(summary.as_dict())
    if analyze and score:
        _print(orch.analyze_run(summary.run_id).as_dict())


@app.command()
def discover(
    domain: str = typer.Argument(..., help="Site domain or URL to discover"),
    max_urls: int | None = typer.Option(None, "--max-urls", help="Cap the discovered inventory"),
    top: int | None = typer.Option(None, "--top", help="Show the top N rows (default: selected only)"),
) -> None:
    """Discover + prioritize a site's URLs and print the ranking — no crawl, no DB.
    Use it to pick the right prioritization.top_n before running a full audit."""
    _bootstrap()
    from .crawl.discovery import discover as discover_site
    from .crawl.prioritize import PageInput, load_prioritization_cfg, prioritize

    result = asyncio.run(discover_site(domain, max_urls=max_urls))
    cfg = load_prioritization_cfg()
    scored = prioritize([PageInput(d.url, d.internal_links) for d in result.urls], cfg)

    typer.echo(f"discovered {len(scored)} url(s) via {result.source}; top_n = {cfg.top_n}")
    rows = scored[:top] if top is not None else [s for s in scored if s.selected]
    for s in rows:
        mark = "*" if s.selected else " "
        typer.echo(f"{mark} {s.rank:>3}. {s.final_score:>8.2f}  {s.page_type:9} {s.url}")


@app.command()
def profile(
    domain: str = typer.Argument(..., help="Site domain or URL to profile"),
    max_urls: int | None = typer.Option(None, "--max-urls", help="Cap discovery before ranking"),
    use_llm: bool = typer.Option(
        False, "--llm/--no-llm", help="Use the LLM for the business-model tiebreak + blueprint synthesis"
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw SiteProfile JSON"),
) -> None:
    """Classify a site and print its AEO STRATEGY — the consultant's answer.

    Site tier (none/single/small/…/enterprise), business model, journey-stage gaps,
    the routed scenario, and a prioritized action plan. Read-only: discovers + ranks the
    site and builds the profile in memory, writing NOTHING to the DB (like `aeo discover`).
    """
    _bootstrap()
    from .crawl.discovery import discover as discover_site
    from .crawl.prioritize import PageInput, load_prioritization_cfg, prioritize
    from .intelligence import build_site_profile
    from .nlp.llm import get_client
    from .pipeline.reference_arch import discovered_pages
    from .processor.coverage_diff import coverage_diff
    from .reference import load_reference
    from .reference.competitor_patterns import CompetitorPatterns
    from .reference.domain_config import load_domain_config
    from .reference.framework import load_framework
    from .reference.generator import generate_blueprint

    cfg = load_prioritization_cfg()
    dc = load_domain_config(domain)
    result = asyncio.run(discover_site(domain, max_urls=max_urls))
    scored = prioritize([PageInput(d.url, d.internal_links) for d in result.urls], cfg)

    # Deterministic in-memory blueprint + coverage (no DB), so the journey engine has
    # missing-node fillers and the scenario router has a coverage signal.
    llm = get_client() if use_llm else None
    framework = load_framework(domain)
    topic = (dc.topic if dc else None) or framework.topic
    blueprint = generate_blueprint(
        topic=topic, framework=framework, patterns=CompetitorPatterns(), llm=llm
    )
    reference = load_reference()
    cov = coverage_diff(blueprint, discovered_pages(scored, reference))
    prof = build_site_profile(domain=domain, discovered=scored, coverage=cov, topic=topic, llm=llm)

    if as_json:
        _print(prof.to_dict())
        return

    d = prof.to_dict()
    cls, biz, jr = d["classification"], d["business_intent"], d["journey"]
    struct_pct = round(float(cls["structure_score"]) * 100)
    typer.echo(f"AEO PROFILE  {domain}   (discovered {len(scored)} pages via {result.source})")
    typer.echo(f"  Scenario      : {d['scenario']}  ->  {d['deliverable']}")
    typer.echo(f"  Site class    : {cls['site_class']}  ({cls['page_count']} pages, structure {struct_pct}%)")
    typer.echo(f"  Business model: {biz['model']}  (confidence {biz['confidence']}, {biz['decided_by']})")
    typer.echo(f"  {d['headline']}")
    typer.echo(f"  Present pages : {', '.join(cls['present_archetypes']) or 'none'}")
    typer.echo(f"  Missing pages : {', '.join(cls['missing_archetypes']) or 'none'}")
    typer.echo(f"  Journey gaps  : {', '.join(jr['gaps']) or 'none'}")
    typer.echo(f"  Coverage      : {cov.coverage_pct}%  ({len(cov.missing)} missing ideal pages)")
    typer.echo("  ACTION PLAN:")
    for a in d["actions"]:
        typer.echo(f"    {a['priority']:>2}. [{a['category']:13}] {a['title']}  ({a['effort']})")


@app.command()
def plan(
    name: str = typer.Argument(..., help="Business name, e.g. 'Acme Security'"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Planned domain (optional for a not-yet-built site)"),
    category: str | None = typer.Option(None, "--category", help="Industry vertical for the Taxonomic Ceiling, e.g. 'healthcare'"),
    topic: str | None = typer.Option(None, "--topic", help="Topic hint (default: derived from the name)"),
    location: str | None = typer.Option(None, "--location", help="Business location (optional)"),
    services: list[str] = typer.Option(None, "--service", help="A service offered (repeatable)"),
    competitors: list[str] = typer.Option(None, "--competitor", help="A competitor domain (repeatable)"),
    goals: list[str] = typer.Option(None, "--goal", help="A business goal (repeatable)"),
    use_llm: bool = typer.Option(False, "--llm/--no-llm", help="Use the LLM for framework + blueprint synthesis"),
    write_config: bool = typer.Option(False, "--write-config", help="Persist the generated framework to config/domains/"),
    bundle: Path | None = typer.Option(None, "--bundle", help="Write a developer-ready asset bundle to this directory"),
    as_json: bool = typer.Option(False, "--json", help="Emit the full plan as JSON"),
) -> None:
    """Generate an AEO Website Blueprint + strategy from a business brief — NO website required.

    Scenario 1: "I want to rank in AI search" with no site yet. Builds the ideal-site
    framework + blueprint from the brief (deterministic; --llm tailors it), treats the
    site as empty so every ideal page is a build target, and routes to the no_website
    strategy + a prioritized build plan. Read-only by default; --write-config persists the
    generated framework to config/domains/ as the onboarding artifact.
    """
    _bootstrap()
    from .intelligence.brief import plan_from_brief
    from .nlp.llm import get_client
    from .reference.business_input import BusinessInput
    from .reference.framework import build_framework, load_framework
    from .reference.framework_bootstrap import (
        bootstrap_framework,
        framework_file_path,
        write_framework,
    )

    brief = BusinessInput(
        name=name, domain=domain, category=category, topic=topic, location=location,
        services=list(services or []), competitors=list(competitors or []), goals=list(goals or []),
    )
    key = brief.key()
    llm = get_client() if use_llm else None

    # Build the brief-tailored framework. In-memory by default (a side-effect-free
    # preview); --write-config persists it like `aeo framework bootstrap`/`aeo onboard`.
    fw_path = framework_file_path(key)
    if fw_path.exists():
        framework = load_framework(key)
        fw_source = "existing"
    else:
        data = bootstrap_framework(key, llm=llm, topic=brief.topic_hint(), category=category)
        if write_config:
            write_framework(key, data)
            framework = load_framework(key)
            fw_source = f"written:{data.get('_generated_by', 'generic-skeleton')}"
        else:
            framework = build_framework(data)
            fw_source = str(data.get("_generated_by", "generic-skeleton"))

    result = plan_from_brief(brief, framework=framework, llm=llm)

    if bundle is not None:
        from .report.packager import build_asset_bundle

        ab = build_asset_bundle(
            blueprint=result.blueprint, coverage=result.coverage,
            profile=result.profile.to_dict(), origin=brief.domain or key, llm=llm,
        )
        written = ab.write(bundle)
        typer.echo(f"wrote {len(written)} file(s) to {bundle}  (bundle: {ab.name})")

    if as_json:
        _print(result.to_dict())
        return

    d = result.to_dict()
    prof, bp = d["profile"], d["blueprint"]
    typer.echo(f"AEO PLAN  {brief.name}  (key={key}, framework={fw_source})")
    typer.echo(f"  Scenario      : {prof['scenario']}  ->  {prof['deliverable']}")
    typer.echo(f"  {prof['headline']}")
    typer.echo(f"  Business model: {prof['business_intent']['model']}  ({prof['business_intent']['decided_by']})")
    typer.echo(
        f"  Ideal site    : {bp['ideal_pages']} pages on topic '{bp['topic']}'  "
        f"(coverage {d['coverage']['pct']}% — greenfield)"
    )
    typer.echo("  IDEAL SITEMAP (build these):")
    for n in bp["sitemap"]:
        cl = f"  [{n['cluster']}]" if n.get("cluster") else ""
        typer.echo(f"    {n['priority']:>4.2f}  [{n['page_type']}/{n['intent']}] {n['slug']:32} {n['title']}{cl}")
    typer.echo("  ACTION PLAN:")
    for a in prof["actions"]:
        typer.echo(f"    {a['priority']:>2}. [{a['category']:13}] {a['title']}  ({a['effort']})")
    typer.echo("next: --write-config to persist the framework as the onboarding artifact, or --json for the full plan.")


@app.command()
def deliverables(
    run_id: int = typer.Option(..., "--run-id", "-r", help="Scored/audited run to package"),
    out: Path = typer.Option(..., "--out", "-o", help="Output directory for the asset bundle"),
    use_llm: bool = typer.Option(False, "--llm/--no-llm", help="Use the LLM for per-page spec prose"),
    draft_limit: int = typer.Option(10, "--draft-limit", help="How many per-page spec sheets to generate"),
) -> None:
    """Build a developer-ready implementation bundle from a run's blueprint + coverage diff.

    Writes sitemap.xml, navigation, content briefs, an internal-linking plan, schema/entity
    recommendations, and a per-page spec sheet (H1 + sections + FAQ + JSON-LD) for the top
    pages — to --out. Reuses the run's pinned blueprint, coverage diff, and SP-1 strategy.
    """
    _bootstrap()
    from .nlp.llm import get_client
    from .processor.coverage_diff import CoverageDiffResult
    from .report.packager import build_asset_bundle
    from .storage.repos import blueprints as blueprints_repo
    from .storage.repos import coverage as coverage_repo

    cov_row = coverage_repo.get(run_id)
    if not cov_row or not cov_row.get("blueprint_id"):
        typer.secho(f"no coverage diff / blueprint for run {run_id} — run an audit first", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    stored = blueprints_repo.get(cov_row["blueprint_id"])
    if stored is None:
        typer.secho(f"blueprint {cov_row['blueprint_id']} not found", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    detail = cov_row.get("detail") or {}
    coverage = CoverageDiffResult.from_detail(detail)
    profile = detail.get("site_profile")
    llm = get_client() if use_llm else None
    ab = build_asset_bundle(
        blueprint=stored.blueprint, coverage=coverage, profile=profile,
        origin=(profile or {}).get("domain"), llm=llm, draft_limit=draft_limit,
    )
    written = ab.write(out)
    typer.echo(f"wrote {len(written)} file(s) to {out}  (bundle: {ab.name})")
    for p in written[:12]:
        typer.echo(f"  {p}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)"),
) -> None:
    """Run the HTTP API (FastAPI) — the SP-4 backend the guided UI calls.

    Requires the optional [api] extra:  pip install -e ".[api]".  Interactive docs at /docs.
    """
    _bootstrap()
    try:
        import uvicorn
    except ImportError as exc:
        typer.secho('the API needs the optional [api] extra:  pip install -e ".[api]"', fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(f"AEO API on http://{host}:{port}  (interactive docs at /docs)")
    uvicorn.run("aeo.api.app:app", host=host, port=port, reload=reload)


@app.command()
def score(run_id: int = typer.Option(..., "--run-id", "-r", help="Run to score")) -> None:
    """Score every extracted-but-unscored page in a run."""
    _bootstrap()
    scored = Orchestrator().score_run(run_id)
    typer.echo(f"scored {scored} page(s) for run {run_id}")


@app.command()
def analyze(
    run_id: int = typer.Option(..., "--run-id", "-r", help="Scored run to analyze"),
    no_persist: bool = typer.Option(False, "--no-persist", help="Compute but don't write to the DB"),
) -> None:
    """Run the back half of the pipeline on a scored run: per page, Dual-Layer Gap
    Analysis → Recommender → Validation (≤3) → per-page report. Each page is
    isolated by the Error Sink. Produces the final per-page deliverable."""
    _bootstrap()
    summary = Orchestrator().analyze_run(run_id, persist=not no_persist)
    _print(summary.as_dict())


@app.command()
def enqueue(
    urls: list[str] | None = typer.Argument(None),
    file: Path | None = typer.Option(None, "--file", "-f"),
    target: str = typer.Option("Securin", "--target", "-t"),
    label: str | None = typer.Option(None, "--label", "-l"),
) -> None:
    """Queue a crawl batch for a worker to pick up."""
    _bootstrap()
    url_list = _collect_urls(urls, file)
    _resolve_target(target)  # validate before enqueuing
    job_id = enqueue_batch(url_list, target, label)
    typer.echo(f"enqueued job {job_id}: {len(url_list)} url(s) for {target}")


@app.command()
def worker(
    max_jobs: int | None = typer.Option(None, "--max-jobs", help="Stop after N jobs (default: run forever)"),
    idle_sleep: float = typer.Option(5.0, "--idle-sleep", help="Seconds to wait when the queue is empty"),
) -> None:
    """Drain the crawl-job queue."""
    _bootstrap()
    processed = Worker(idle_sleep=idle_sleep).run_forever(max_jobs=max_jobs)
    typer.echo(f"processed {processed} job(s)")


@app.command()
def status(run_id: int | None = typer.Option(None, "--run-id", "-r")) -> None:
    """Show DB health, queue depth, and (optionally) a run's score report."""
    _bootstrap()
    typer.echo(f"database : {'ok' if health_check() else 'UNREACHABLE'}")
    typer.echo(f"queue    : {jobs_repo.stats()}")
    if run_id is not None:
        _print(scores_repo.run_report(run_id))


@app.command()
def trace(
    page_id: int = typer.Argument(..., help="crawled_pages.id to trace"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a journey"),
) -> None:
    """Dump a page's agent journey (every traced step, in order)."""
    _bootstrap()
    rows = traces_repo.for_page(page_id)
    if not rows:
        typer.echo(f"no traces for page {page_id}")
        return
    if as_json:
        _print(rows)
        return
    for r in rows:
        ts = str(r.get("created_at", ""))[:19]
        dur = r.get("duration_ms")
        dur_s = f"{dur}ms" if dur is not None else "-"
        model = f" [{r['model']}]" if r.get("model") else ""
        line = (f"{ts}  {r['agent']:12} {(r.get('step') or '-'):18} "
                f"{r['status']:8} {dur_s:>8}{model}")
        typer.echo(line)
        if r.get("error"):
            typer.echo(f"    ! {r['error']}")


@app.command()
def report(
    target: str | None = typer.Argument(None, help="Client/competitor name (latest run)"),
    run_id: int | None = typer.Option(None, "--run-id", "-r", help="Limit to a run"),
    page_id: int | None = typer.Option(None, "--page-id", "-p", help="A single page's report"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON instead of rendered text"),
    pdf: Path | None = typer.Option(None, "--pdf", help="Write the report(s) to a PDF file (client-ready deliverable)"),
) -> None:
    """Render the optimized per-page AEO/SEO report(s) — the final deliverable.

    Scope precedence: --page-id, then TARGET (its latest run, or --run-id), then
    --run-id alone. Surfaces each page's Human-Review status. Use --pdf to export.
    """
    _bootstrap()
    if page_id is not None:
        rows = reports_repo.for_page(page_id)
    elif target is not None:
        tgt = _resolve_target(target)
        rows = reports_repo.for_target(tgt.id, tgt.kind, run_id=run_id)
    elif run_id is not None:
        rows = reports_repo.for_run(run_id)
    else:
        raise typer.BadParameter("provide a TARGET name, --run-id, or --page-id")

    if not rows:
        typer.echo("no reports found for that scope")
        return
    if pdf is not None:
        from .report.pdf import PdfUnavailable, write_page_reports_pdf
        try:
            out = write_page_reports_pdf(rows, str(pdf))
        except PdfUnavailable as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        typer.echo(f"wrote {out}  ({len(rows)} report(s))")
        return
    if as_json:
        _print(rows)
        return

    for row in rows:
        typer.echo(render_report(row))
        typer.echo("")

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["review_status"]] = counts.get(row["review_status"], 0) + 1
    tally = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    typer.echo(f"{len(rows)} report(s): {tally}")


@app.command(name="audit-cycle")
def audit_cycle(
    domain: str = typer.Argument(..., help="Site domain to audit end-to-end"),
    target: str = typer.Option("Securin", "--target", "-t", help="Client name"),
    label: str | None = typer.Option(None, "--label", "-l", help="Run label"),
    max_urls: int | None = typer.Option(None, "--max-urls", help="Cap discovery before ranking"),
) -> None:
    """The v4 Weekly Audit Loop: discover -> blueprint -> coverage diff -> crawl ->
    score -> analyze -> site report. This is the entrypoint the systemd timer / cron
    in ops/ invokes weekly."""
    _bootstrap()
    tgt = _resolve_target(target)
    result = asyncio.run(Orchestrator().audit_cycle(domain, target=tgt, label=label, max_urls=max_urls))
    _print(result)


@app.command(name="verify-milestones")
def verify_milestones(
    domain: str = typer.Argument(..., help="Client site domain to re-scrape + verify"),
    target: str = typer.Option(..., "--target", "-t", help="Client name (must own the milestones)"),
    max_urls: int | None = typer.Option(None, "--max-urls", help="Cap discovery before checking"),
) -> None:
    """Re-scrape a client's live site and auto-verify its implementation milestones.

    The standalone form of the weekly verification crawl: discovers the site's current
    URL inventory, re-reads the homepage + key pages, and flips any pending milestone task
    whose recommended artifact (a page slug / offering / heading) is now live to
    'verified_completed'. The same check `aeo audit-cycle` runs at the end of its loop."""
    _bootstrap()
    from .crawl.discovery import discover as discover_site
    from .pipeline.milestone_audit import verify_client_milestones
    from .storage.repos import milestones as milestones_repo

    tgt = _resolve_target(target)
    if tgt.kind != "client":
        typer.secho(f"{target!r} is a competitor — milestones are tracked per client", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if not milestones_repo.has_milestones(tgt.id):
        typer.secho(f"no milestones for {tgt.name} — generate a plan for {domain} first", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    async def _run() -> dict:
        discovery = await discover_site(domain, max_urls=max_urls)
        slugs = [d.url for d in discovery.urls]
        return await verify_client_milestones(tgt.id, domain, discovered_slugs=slugs)

    result = asyncio.run(_run())
    _print(result)
    typer.echo(
        f"checked {result['checked']} pending task(s); "
        f"newly verified {result['newly_verified']}"
    )


# ── v4 Reference Architecture blueprint ──────────────────────────────────────
blueprint_app = typer.Typer(add_completion=False, help="v4 Reference Architecture blueprints.")
app.add_typer(blueprint_app, name="blueprint")


@blueprint_app.command("generate")
def blueprint_generate(
    topic: str | None = typer.Option(None, "--topic", help="Topic (default: framework topic)"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Use the LLM for L3 synthesis"),
) -> None:
    """Generate (or reuse) the versioned blueprint for a topic and persist it.
    Combines competitor patterns (L1) + framework (L2) [+ LLM synthesis (L3)]."""
    _bootstrap()
    from .nlp.llm import get_client
    from .pipeline.reference_arch import build_competitor_patterns
    from .reference.framework import load_framework
    from .reference.generator import generate_blueprint
    from .storage.repos import blueprints as blueprints_repo

    framework = load_framework()
    topic = topic or framework.topic
    patterns = build_competitor_patterns(framework.required_entities)
    llm = get_client() if use_llm else None
    bp = generate_blueprint(topic=topic, framework=framework, patterns=patterns, llm=llm)
    stored = blueprints_repo.save_versioned(bp)
    typer.echo(
        f"blueprint topic={topic} version={stored.blueprint.version} "
        f"{'(reused)' if stored.reused else '(new)'} generator={stored.blueprint.generator} "
        f"nodes={len(stored.blueprint.sitemap)} competitors={len(patterns.domains)}"
    )


@blueprint_app.command("show")
def blueprint_show(
    topic: str | None = typer.Option(None, "--topic", help="Topic (default: framework topic)"),
    version: int | None = typer.Option(None, "--version", help="Specific version (default: latest)"),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw blueprint JSON"),
) -> None:
    """Print a stored blueprint's ideal sitemap + coverage map."""
    _bootstrap()
    from .reference.framework import load_framework
    from .storage.repos import blueprints as blueprints_repo

    topic = topic or load_framework().topic
    stored = blueprints_repo.by_version(topic, version) if version else blueprints_repo.latest(topic)
    if stored is None:
        typer.echo(f"no blueprint for topic {topic!r}" + (f" v{version}" if version else ""))
        return
    bp = stored.blueprint
    if as_json:
        _print(bp.to_jsonb())
        return
    typer.echo(f"BLUEPRINT  {bp.topic}  v{bp.version}  [{bp.generator}]  ({len(bp.sitemap)} pages)")
    for n in bp.sitemap:
        ents = f"  entities={', '.join(n.required_entities)}" if n.required_entities else ""
        typer.echo(f"  {n.priority:>4.2f}  [{n.page_type}/{n.intent}] {n.slug:32} {n.title}{ents}")
    if bp.coverage.clusters:
        typer.echo("CLUSTERS")
        for c in bp.coverage.clusters:
            typer.echo(f"  {c.name:22} pillar={c.pillar_slug} min_pages={c.min_pages}")


# ── Per-domain AEO frameworks (make it work for ANY website) ─────────────────
framework_app = typer.Typer(add_completion=False, help="Per-domain ideal-site frameworks.")
app.add_typer(framework_app, name="framework")


@framework_app.command("bootstrap")
def framework_bootstrap_cmd(
    domain: str = typer.Argument(..., help="Domain to generate an ideal-site framework for, e.g. acme.com"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="Tailor to the topic via the LLM (else a generic skeleton)"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing framework file"),
    topic: str | None = typer.Option(None, "--topic", help="Topic hint (default: derived from the domain)"),
    category: str | None = typer.Option(
        None, "--category",
        help="Industry vertical for the Taxonomic Ceiling, e.g. 'healthcare', 'personal finance', 'legal services'",
    ),
) -> None:
    """Generate config/domains/<domain>.framework.yaml so ANY website can be AEO-optimized.

    Writes a per-domain ideal-site taxonomy the blueprint + site-level coverage diff measure
    against. Deterministic generic skeleton by default; --llm tailors it to the site's real
    topic (real entities, topic clusters, seed questions), re-validated against the contract.
    --category fuses an industry Taxonomic Ceiling (HIPAA for healthcare, SEC/CFPB for finance,
    …) into the required entities. Review the file, then `aeo add-target` + `aeo audit-cycle`."""
    _bootstrap()
    from .nlp.llm import get_client
    from .reference.domain_config import normalize_domain
    from .reference.framework_bootstrap import (
        bootstrap_framework,
        framework_file_path,
        write_framework,
    )

    path = framework_file_path(domain)
    if path.exists() and not overwrite:
        typer.secho(f"{path} already exists — use --overwrite to replace it", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    llm = get_client() if use_llm else None
    data = bootstrap_framework(domain, llm=llm, topic=topic, category=category)
    out = write_framework(domain, data)
    ideal_pages = sum(1 + len(c.get("supporting", [])) for c in data.get("clusters", [])) + len(data.get("standalone_nodes", []))
    host = normalize_domain(domain)
    typer.echo(f"wrote {out}")
    typer.echo(
        f"  topic={data.get('topic')}  entities={len(data.get('required_entities', []))}  "
        f"clusters={len(data.get('clusters', []))}  ideal_pages={ideal_pages}  "
        f"source={data.get('_generated_by', 'generic-skeleton')}"
    )
    typer.echo(f"next:  aeo add-target <Name> {host}   &&   aeo audit-cycle {host} -t <Name>")


@app.command()
def coverage(run_id: int = typer.Option(..., "--run-id", "-r", help="Run to show the coverage diff for")) -> None:
    """Show the site-level Coverage Diff for a run (missing pages + thin clusters)."""
    _bootstrap()
    from .storage.repos import coverage as coverage_repo

    row = coverage_repo.get(run_id)
    if not row:
        typer.echo(f"no coverage diff for run {run_id}")
        return
    detail = row.get("detail") or {}
    typer.echo(
        f"COVERAGE  topic={detail.get('topic')} blueprint=v{detail.get('blueprint_version')}  "
        f"{row['coverage_pct']}%  missing={row['missing_count']} thin={row['thin_count']}"
    )
    for m in detail.get("missing", [])[:40]:
        typer.echo(f"  MISSING  {m.get('priority'):>4}  [{m.get('page_type')}] {m.get('slug')}  {m.get('title')}")
    for t in detail.get("thin_clusters", []):
        typer.echo(f"  THIN     {t.get('cluster')}: {t.get('present_count')}/{t.get('min_pages')}")


@app.command(name="site-report")
def site_report(
    run_id: int = typer.Option(..., "--run-id", "-r", help="Run to render the site report for"),
    pdf: Path | None = typer.Option(None, "--pdf", help="Write the site report to a PDF file"),
) -> None:
    """Render the site-level AEO report (coverage + per-page rollup) for a run."""
    _bootstrap()
    from .report import render_site_report
    from .storage.repos import site_reports as site_reports_repo

    row = site_reports_repo.for_run(run_id)
    if not row:
        typer.echo(f"no site report for run {run_id}")
        return
    if pdf is not None:
        from .report.pdf import PdfUnavailable, write_site_report_pdf
        try:
            out = write_site_report_pdf(row, str(pdf))
        except PdfUnavailable as exc:
            typer.secho(str(exc), fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        typer.echo(f"wrote {out}")
        return
    typer.echo(render_site_report(row))


@app.command()
def refinements(
    propose: bool = typer.Option(False, "--propose", help="Compute proposals from cited pages and save them"),
    status: str | None = typer.Option(None, "--status", help="Filter listed proposals by status"),
    accept: int | None = typer.Option(None, "--accept", help="Mark a refinement id accepted"),
    reject: int | None = typer.Option(None, "--reject", help="Mark a refinement id rejected"),
) -> None:
    """Validated-wins loop: list (or propose) human-gated criteria-target refinements
    from pages that provably get cited. The system never auto-applies them."""
    _bootstrap()
    from .reference.feedback import propose_criteria_refinements
    from .storage.repos import feedback as feedback_repo

    if accept is not None:
        feedback_repo.set_refinement_status(accept, "accepted")
        typer.echo(f"refinement {accept} accepted")
        return
    if reject is not None:
        feedback_repo.set_refinement_status(reject, "rejected")
        typer.echo(f"refinement {reject} rejected")
        return
    if propose:
        observations = feedback_repo.recent_observations()
        proposals = propose_criteria_refinements(observations)
        for p in proposals:
            rid = feedback_repo.save_refinement(p)
            typer.echo(f"proposed #{rid}: {p.criterion} {p.current_target}->{p.proposed_target}")
        if not proposals:
            typer.echo("no refinement proposals (insufficient or inconclusive citation signal)")
        return

    for row in feedback_repo.list_refinements(status):
        typer.echo(
            f"#{row['id']} [{row['status']}] {row['criterion']} "
            f"{row['current_target']}->{row['proposed_target']}  {row['rationale']}"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
