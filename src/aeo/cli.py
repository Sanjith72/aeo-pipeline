from __future__ import annotations
import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from aeo.config import get_settings
from aeo.utils.observability import configure_logging, configure_tracing

app = typer.Typer(name="aeo", help="Answer Engine Optimization pipeline", add_completion=False)
console = Console()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@app.callback()
def _setup() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    if s.otel_endpoint:
        configure_tracing(s.otel_service_name, s.otel_endpoint)


# ── run ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    domain: Optional[str] = typer.Argument(None, help="Single domain to process"),
    all_domains: bool = typer.Option(False, "--all", help="Process all PIPELINE_DOMAINS"),
    regenerate: bool = typer.Option(
        False, "--regenerate-blueprint", help="Force-regenerate the reference blueprint"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run full pipeline in memory — no DB writes, capped at 10 pages"
    ),
) -> None:
    """Run the full AEO pipeline: crawl → coverage diff → recommend → validate."""
    if dry_run:
        if all_domains:
            console.print("[red]--dry-run does not support --all[/red]")
            raise typer.Exit(1)
        if not domain:
            console.print("[red]Provide a domain for --dry-run[/red]")
            raise typer.Exit(1)
        _run(_dry_run_pipeline(domain))
    else:
        _run(_pipeline(domain, all_domains, regenerate))


# ── db-init ───────────────────────────────────────────────────────────────────

@app.command(name="db-init")
def db_init() -> None:
    """Initialize (or migrate) the PostgreSQL schema."""
    _run(_init_db())


# ── report ────────────────────────────────────────────────────────────────────

@app.command()
def report(
    domain: str = typer.Argument(...),
    limit: int = typer.Option(20, help="Number of recommendations to display"),
) -> None:
    """Print the latest validated recommendations for a domain."""
    _run(_show_report(domain, limit))


# ── blueprint ────────────────────────────────────────────────────────────────

@app.command(name="blueprint")
def blueprint_show(domain: str = typer.Argument(...)) -> None:
    """Print the current reference blueprint for a domain."""
    _run(_show_blueprint(domain))


# ── async implementations ─────────────────────────────────────────────────────

async def _init_db() -> None:
    import asyncpg
    from pathlib import Path

    settings = get_settings()
    schema = (Path(__file__).parent / "db" / "schema.sql").read_text()
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute(schema)
        console.print("[green]Schema initialised.[/green]")
    finally:
        await conn.close()


async def _pipeline(domain: str | None, all_domains: bool, regenerate: bool) -> None:
    from aeo.db import create_pool
    from aeo.db.queries import (
        finish_run,
        get_latest_blueprint,
        insert_recommendation,
        insert_run,
        insert_validation_result,
        upsert_blueprint,
    )
    from aeo.agents.reference_generator import generate_blueprint
    from aeo.agents.processor import process_domain
    from aeo.agents.recommender import generate_recommendations
    from aeo.agents.validator import validate_recommendations

    settings = get_settings()
    domains: list[str] = settings.pipeline_domains if all_domains else ([domain] if domain else [])
    if not domains:
        console.print("[red]Provide a domain or --all[/red]")
        raise typer.Exit(1)

    pool = await create_pool(settings.database_url)
    try:
        for d in domains:
            run_id = await insert_run(pool, d)
            console.rule(f"[bold cyan]{d}[/bold cyan]")

            with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:

                t = p.add_task("Fetching blueprint…", total=None)
                blueprint = (None if regenerate else await get_latest_blueprint(pool, d))
                if blueprint is None:
                    blueprint = await generate_blueprint(d)
                    await upsert_blueprint(pool, blueprint)
                p.update(t, description=f"Blueprint ready — {len(blueprint.target_queries)} queries")

                t = p.add_task(f"Crawling https://{d}…", total=None)
                diffs, pages = await process_domain(d, f"https://{d}", blueprint, pool, run_id)
                p.update(t, description=f"Crawled — {len(diffs)} changed pages processed")

                t = p.add_task("Generating recommendations…", total=None)
                recs = await generate_recommendations(diffs, blueprint)
                p.update(t, description=f"{len(recs)} recommendations generated")

                t = p.add_task("Validating…", total=None)
                pages_by_url = {pg.url: pg for pg in pages}
                validated = await validate_recommendations(recs, pages_by_url)
                valid = [(r, v) for r, v in validated if v.is_valid]
                p.update(t, description=f"{len(valid)}/{len(validated)} passed validation")

            for rec, val in validated:
                await insert_recommendation(pool, run_id, rec)
                await insert_validation_result(pool, val)

            await finish_run(pool, run_id, pages_crawled=len(pages), pages_changed=len(diffs))
            _print_run_summary(d, diffs, valid)
    finally:
        await pool.close()


def _print_run_summary(domain: str, diffs, valid_pairs) -> None:
    if diffs:
        avg = sum(d.coverage_score for d in diffs) / len(diffs)
        console.print(f"Avg coverage score: [yellow]{avg:.1%}[/yellow]")

    if not valid_pairs:
        console.print("[green]No actionable recommendations.[/green]")
        return

    tbl = Table(title="Top Recommendations", show_header=True, header_style="bold")
    tbl.add_column("Priority", width=8)
    tbl.add_column("Type", width=10)
    tbl.add_column("Title")
    tbl.add_column("Impact", width=7)

    colour = {"high": "red", "medium": "yellow", "low": "green"}
    for rec, _ in valid_pairs[:10]:
        c = colour[rec.priority]
        tbl.add_row(
            f"[{c}]{rec.priority}[/{c}]",
            rec.type,
            rec.title,
            f"{rec.impact_estimate:.0%}",
        )
    console.print(tbl)


async def _show_report(domain: str, limit: int) -> None:
    from aeo.db import create_pool
    from aeo.db.queries import get_run_recommendations

    pool = await create_pool(get_settings().database_url)
    try:
        rows = await get_run_recommendations(pool, domain, limit)
    finally:
        await pool.close()

    if not rows:
        console.print(f"No recommendations found for [bold]{domain}[/bold]")
        return

    tbl = Table(title=f"Recommendations: {domain}", show_header=True)
    tbl.add_column("Priority")
    tbl.add_column("Type")
    tbl.add_column("Title")
    tbl.add_column("Valid")
    tbl.add_column("WC")
    tbl.add_column("H1?")
    tbl.add_column("JSON-LD")

    for row in rows:
        tbl.add_row(
            row["priority"] or "-",
            row["type"] or "-",
            row["title"],
            "✓" if row["is_valid"] else "✗",
            "✓" if row["word_count_ok"] else "✗",
            "✓" if row["h1_question_ok"] else "✗",
            "✓" if row["json_ld_ok"] else "✗",
        )
    console.print(tbl)


async def _dry_run_pipeline(domain: str, max_pages: int = 10) -> None:
    import asyncio as _asyncio

    from aeo.agents.coverage_diff import compute_coverage_diff
    from aeo.agents.crawler import crawl_site
    from aeo.agents.recommender import generate_recommendations
    from aeo.agents.reference_generator import generate_blueprint
    from aeo.agents.validator import validate_recommendations

    console.rule(f"[bold yellow]DRY RUN — {domain}[/bold yellow] (no DB writes, ≤{max_pages} pages)")

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:

        t = p.add_task("Generating reference blueprint via Gemini…", total=None)
        blueprint = await generate_blueprint(domain)
        p.update(t, description=f"Blueprint — {len(blueprint.target_queries)} queries, {len(blueprint.required_entities)} entities")

        t = p.add_task(f"Crawling https://{domain} (cap: {max_pages} pages)…", total=None)
        pages = await crawl_site(f"https://{domain}", max_pages=max_pages)
        p.update(t, description=f"Crawled {len(pages)} page(s)")

        t = p.add_task("Computing coverage diffs via Ollama…", total=None)
        diff_results = await _asyncio.gather(
            *[compute_coverage_diff(pg, blueprint) for pg in pages],
            return_exceptions=True,
        )
        diffs = [d for d in diff_results if not isinstance(d, BaseException)]
        p.update(t, description=f"Coverage diffs: {len(diffs)} computed")

        t = p.add_task("Generating recommendations via Ollama…", total=None)
        recs = await generate_recommendations(diffs, blueprint)
        p.update(t, description=f"{len(recs)} recommendations")

        t = p.add_task("Validating (deterministic)…", total=None)
        pages_by_url = {pg.url: pg for pg in pages}
        validated = await validate_recommendations(recs, pages_by_url)
        valid = [(r, v) for r, v in validated if v.is_valid]
        p.update(t, description=f"{len(valid)}/{len(validated)} passed")

    # ── Blueprint summary ──────────────────────────────────────────────────────
    console.print()
    bp_tbl = Table(title=f"Reference Blueprint — {domain}", show_header=False, box=None)
    bp_tbl.add_column("Key", style="bold cyan", width=20)
    bp_tbl.add_column("Value")
    bp_tbl.add_row("Target queries", str(len(blueprint.target_queries)))
    bp_tbl.add_row("Required entities", ", ".join(blueprint.required_entities[:6]) + ("…" if len(blueprint.required_entities) > 6 else ""))
    bp_tbl.add_row("Schema types", ", ".join(blueprint.schema_types))
    bp_tbl.add_row("Freshness", f"every {blueprint.freshness_days} days")
    console.print(bp_tbl)

    # ── Per-page coverage table ────────────────────────────────────────────────
    if diffs:
        console.print()
        cov_tbl = Table(title="Coverage by Page", show_header=True, header_style="bold")
        cov_tbl.add_column("URL", no_wrap=False)
        cov_tbl.add_column("Score", width=7, justify="right")
        cov_tbl.add_column("Queries", width=8, justify="right")
        cov_tbl.add_column("Entities", width=9, justify="right")
        cov_tbl.add_column("Schema ✗", width=9)
        for diff in sorted(diffs, key=lambda d: d.coverage_score):
            q_cov = sum(1 for q in diff.query_coverage if q.covered)
            e_cov = sum(1 for e in diff.entity_coverage if e.present)
            score_colour = "green" if diff.coverage_score >= 0.7 else "yellow" if diff.coverage_score >= 0.4 else "red"
            cov_tbl.add_row(
                diff.url,
                f"[{score_colour}]{diff.coverage_score:.0%}[/{score_colour}]",
                f"{q_cov}/{len(diff.query_coverage)}",
                f"{e_cov}/{len(diff.entity_coverage)}",
                ", ".join(diff.schema_types_missing[:3]) or "—",
            )
        console.print(cov_tbl)

    # ── Recommendations ────────────────────────────────────────────────────────
    if validated:
        console.print()
        rec_tbl = Table(title="Recommendations (dry-run)", show_header=True, header_style="bold")
        rec_tbl.add_column("✓", width=3)
        rec_tbl.add_column("Pri", width=6)
        rec_tbl.add_column("Type", width=10)
        rec_tbl.add_column("Title")
        rec_tbl.add_column("WC", width=3)
        rec_tbl.add_column("H1?", width=4)
        rec_tbl.add_column("JLD", width=4)
        colour = {"high": "red", "medium": "yellow", "low": "green"}
        for rec, val in validated:
            c = colour[rec.priority]
            rec_tbl.add_row(
                "✓" if val.is_valid else "✗",
                f"[{c}]{rec.priority}[/{c}]",
                rec.type,
                rec.title,
                "✓" if val.word_count_ok else "✗",
                "✓" if val.h1_question_ok else "✗",
                "✓" if val.json_ld_ok else "✗",
            )
        console.print(rec_tbl)

    # ── Summary line ───────────────────────────────────────────────────────────
    console.print()
    if diffs:
        avg = sum(d.coverage_score for d in diffs) / len(diffs)
        console.print(f"Avg coverage score: [bold]{avg:.1%}[/bold]  |  Valid recs: [bold]{len(valid)}[/bold]/{len(validated)}  |  [yellow]Nothing written to DB.[/yellow]")
    else:
        console.print("[yellow]No pages could be diffed. Nothing written to DB.[/yellow]")


async def _show_blueprint(domain: str) -> None:
    from aeo.db import create_pool
    from aeo.db.queries import get_latest_blueprint

    pool = await create_pool(get_settings().database_url)
    try:
        bp = await get_latest_blueprint(pool, domain)
    finally:
        await pool.close()

    if bp is None:
        console.print(f"[red]No blueprint for {domain}. Run: aeo run {domain}[/red]")
        return

    console.print(f"[bold]Blueprint: {domain} v{bp.version}[/bold]")
    console.print(f"\n[underline]Target queries[/underline] ({len(bp.target_queries)}):")
    for q in bp.target_queries:
        console.print(f"  • {q}")
    console.print(f"\n[underline]Required entities[/underline]: {', '.join(bp.required_entities)}")
    console.print(f"[underline]Schema types[/underline]: {', '.join(bp.schema_types)}")
    console.print(f"[underline]Freshness[/underline]: every {bp.freshness_days} days")
