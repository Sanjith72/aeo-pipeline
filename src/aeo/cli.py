"""
Command-line interface (``aeo …``).

Thin layer over the pipeline + repos — every command bootstraps logging, then
delegates. Commands:

    aeo migrate                 apply pending DB migrations
    aeo targets                 list configured clients & competitors
    aeo audit  DOMAIN -t NAME   discover → prioritize → crawl → extract → score
    aeo discover DOMAIN         discover + rank a site's URLs (no crawl, no DB)
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
import json
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
) -> None:
    """Site Discovery → Page Prioritization (top-N) → crawl → extract → score
    [→ analyze]. The single-command path from a bare domain to per-page reports."""
    _bootstrap()
    tgt = _resolve_target(target)
    orch = Orchestrator()
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
) -> None:
    """Render the optimized per-page AEO/SEO report(s) — the final deliverable.

    Scope precedence: --page-id, then TARGET (its latest run, or --run-id), then
    --run-id alone. Surfaces each page's Human-Review status.
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
def site_report(run_id: int = typer.Option(..., "--run-id", "-r", help="Run to render the site report for")) -> None:
    """Render the site-level AEO report (coverage + per-page rollup) for a run."""
    _bootstrap()
    from .report import render_site_report
    from .storage.repos import site_reports as site_reports_repo

    row = site_reports_repo.for_run(run_id)
    if not row:
        typer.echo(f"no site report for run {run_id}")
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
