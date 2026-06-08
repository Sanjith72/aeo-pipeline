"""
Site-level report assembly — the v4 second deliverable (beside the per-page report).

Where the per-page report answers "is this room up to code?", the site report
answers "which rooms are missing, and how does the whole house score?". It folds
the Coverage Diff (missing pages + thin clusters → net-new content briefs) and a
per-page rollup (score distribution, worst pages, review tally) into one record,
pinned to the blueprint version it was measured against.

Pure: transforms already-computed objects (a :class:`Blueprint`, a
:class:`CoverageDiffResult`, and per-page summary rows) into a ``SiteReport``.
Persistence is a thin repo helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..processor.coverage_diff import CoverageDiffResult
from ..reference.blueprint import Blueprint


@dataclass(slots=True)
class SiteReport:
    run_id: int
    target_id: int | None
    blueprint_id: int | None
    summary: str
    sections: dict[str, Any]


def _page_rollup(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Distribution + worst pages + review tally from per-page summary rows.
    Each row: {url, total, max_possible, priority_tier, review_status}."""
    if not pages:
        return {"pages": 0, "avg_score_pct": 0.0, "by_priority": {}, "by_review": {}, "worst_pages": []}

    pcts = []
    by_priority: dict[str, int] = {}
    by_review: dict[str, int] = {}
    for p in pages:
        mx = p.get("max_possible") or 0
        total = p.get("total") or 0
        pcts.append(round(total / mx * 100, 1) if mx else 0.0)
        by_priority[p.get("priority_tier", "?")] = by_priority.get(p.get("priority_tier", "?"), 0) + 1
        rs = p.get("review_status", "?")
        by_review[rs] = by_review.get(rs, 0) + 1

    worst = sorted(pages, key=lambda p: (p.get("total") or 0))[:10]
    worst_rows = [
        {"url": p.get("url"), "total": p.get("total"), "priority_tier": p.get("priority_tier")}
        for p in worst
    ]
    return {
        "pages": len(pages),
        "avg_score_pct": round(sum(pcts) / len(pcts), 1),
        "by_priority": by_priority,
        "by_review": by_review,
        "worst_pages": worst_rows,
    }


def new_page_briefs(coverage: CoverageDiffResult) -> list[dict[str, Any]]:
    """Turn missing blueprint nodes into net-new content briefs (the site-level
    'missing-page recommendations'), highest blueprint-priority first."""
    return [
        {
            "slug": m.slug,
            "title": m.title,
            "page_type": m.page_type,
            "intent": m.intent,
            "journey_stage": m.journey_stage,
            "cluster": m.cluster,
            "priority": m.priority,
            "required_entities": m.required_entities,
            "seed_questions": m.seed_questions,
            "why": m.rationale,
        }
        for m in coverage.missing_by_priority()
    ]


def build_site_report(
    *,
    blueprint: Blueprint,
    coverage: CoverageDiffResult,
    pages: list[dict[str, Any]],
    run_id: int,
    target_id: int | None = None,
    blueprint_id: int | None = None,
    llm: Any = None,
    origin: str | None = None,
    draft_limit: int = 0,
) -> SiteReport:
    """Assemble the site-level AEO report from the blueprint, coverage diff, and
    per-page summaries.

    When ``draft_limit > 0`` the top missing pages get a ready-to-publish ``draft``
    (H1 + headers + body prose + JSON-LD) — LLM-authored when ``llm`` is enabled, a
    deterministic scaffold otherwise. ``origin`` (the site's base URL/domain) is used to
    build absolute URLs in the drafted JSON-LD. Defaults keep the builder a pure
    transform (no drafting) for tests and lightweight callers."""
    rollup = _page_rollup(pages)
    briefs = new_page_briefs(coverage)
    if draft_limit > 0 and briefs:
        # Local import: the drafter pulls in the recommender + LLM client, which the
        # pure default path (draft_limit=0) must not require.
        from ..recommender.draft import draft_site_pages

        briefs = draft_site_pages(
            briefs, topic=blueprint.topic, llm=llm, origin=origin, limit=draft_limit
        )
    thin = [
        {"cluster": t.name, "present": t.present_count, "target": t.min_pages, "shortfall": t.shortfall}
        for t in coverage.thin_clusters
    ]

    sections: dict[str, Any] = {
        "overview": {
            "topic": blueprint.topic,
            "blueprint_version": blueprint.version,
            "blueprint_generator": blueprint.generator,
            "coverage_pct": coverage.coverage_pct,
            "ideal_pages": coverage.total_nodes,
            "covered_pages": coverage.matched_count,
            "missing_pages": len(coverage.missing),
            "pages_analyzed": rollup["pages"],
        },
        "coverage_gaps": {
            "missing_count": len(coverage.missing),
            "thin_clusters": thin,
            "new_page_recommendations": briefs,
        },
        "page_rollup": rollup,
    }

    summary = (
        f"{blueprint.topic}: blueprint v{blueprint.version} — site covers "
        f"{coverage.matched_count}/{coverage.total_nodes} ideal pages ({coverage.coverage_pct}%). "
        f"{len(coverage.missing)} missing page(s), {len(thin)} thin cluster(s). "
        f"{rollup['pages']} page(s) analyzed, avg {rollup['avg_score_pct']}%."
    )

    return SiteReport(
        run_id=run_id,
        target_id=target_id,
        blueprint_id=blueprint_id,
        summary=summary,
        sections=sections,
    )


def render_site_report(report: SiteReport | dict[str, Any]) -> str:
    """Plain-text rendering of a site report — what ``aeo site-report`` prints."""
    if isinstance(report, SiteReport):
        summary, sections = report.summary, report.sections
    else:
        summary = report.get("summary") or ""
        sections = report.get("sections") or {}

    rule = "=" * 72
    o = sections.get("overview", {}) or {}
    cov = sections.get("coverage_gaps", {}) or {}
    roll = sections.get("page_rollup", {}) or {}

    lines = [
        rule,
        f"SITE AEO REPORT  -  {o.get('topic', '?')} (blueprint v{o.get('blueprint_version', '?')})",
        rule,
        summary,
        "",
        "COVERAGE",
        f"  Ideal pages   : {o.get('ideal_pages', '?')}",
        f"  Covered       : {o.get('covered_pages', '?')}  ({o.get('coverage_pct', '?')}%)",
        f"  Missing       : {o.get('missing_pages', '?')}",
    ]

    thin = cov.get("thin_clusters", []) or []
    if thin:
        lines.append("  Thin clusters :")
        for t in thin:
            lines.append(f"    - {t['cluster']:22} {t['present']}/{t['target']} (need {t['shortfall']} more)")

    briefs = cov.get("new_page_recommendations", []) or []
    if briefs:
        lines += ["", "NET-NEW CONTENT (missing pages, priority order)"]
        for b in briefs[:20]:
            qs = f"  e.g. {b['seed_questions'][0]}" if b.get("seed_questions") else ""
            lines.append(f"  - [{b['page_type']}] {b['slug']}  ({b.get('priority')}){qs}")

    lines += [
        "",
        "PAGE ROLLUP",
        f"  Pages analyzed : {roll.get('pages', 0)}  (avg {roll.get('avg_score_pct', 0)}%)",
        f"  By priority    : {roll.get('by_priority', {})}",
        f"  By review      : {roll.get('by_review', {})}",
    ]
    worst = roll.get("worst_pages", []) or []
    if worst:
        lines.append("  Worst pages    :")
        for w in worst[:5]:
            lines.append(f"    - {w.get('total')}  {w.get('url')}")
    lines.append(rule)
    return "\n".join(lines)
