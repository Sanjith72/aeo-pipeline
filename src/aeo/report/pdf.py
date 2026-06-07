"""
PDF rendering of the AEO deliverables (per-page report + site report).

Structured (not text-dump): reads the same ``sections`` payload the text renderer uses
and lays it out with reportlab — a client-ready PDF. reportlab is an OPTIONAL dependency
(``pip install -e ".[pdf]"``); the import is lazy so the rest of the tool never needs it.
"""

from __future__ import annotations

from typing import Any

from .builder import PageReport
from .site_builder import SiteReport


class PdfUnavailable(RuntimeError):
    """Raised when reportlab is not installed."""


def _require_reportlab():
    try:
        import reportlab  # noqa: F401
    except Exception as exc:  # pragma: no cover - exercised only without the extra
        raise PdfUnavailable(
            "PDF export needs reportlab — install it with:  pip install -e \".[pdf]\""
        ) from exc


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=15, spaceAfter=4,
                                 textColor=colors.HexColor("#1F3864")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=3,
                              textColor=colors.HexColor("#2E5496")),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9, leading=12),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=8, leading=10),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=8, leading=10),
    }


def _esc(s: Any) -> str:
    import html
    return html.escape(str(s))


def _kv_table(rows: list[tuple[str, Any]], st, page_w):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(f"<b>{_esc(k)}</b>", st["small"]), Paragraph(_esc(v), st["small"])] for k, v in rows]
    t = Table(data, colWidths=[page_w * 0.28, page_w * 0.72])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E3E3E3")),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _page_report_flowables(report: PageReport | dict[str, Any], st, page_w) -> list:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if isinstance(report, PageReport):
        summary, sections, review = report.summary, report.sections, report.review_status
    else:
        summary = report.get("summary") or ""
        sections = report.get("sections") or {}
        review = report.get("review_status") or "pending"
    o = sections.get("overview", {}) or {}

    out = [Paragraph(f"AEO/SEO Report — {_esc(o.get('url', 'page'))}", st["title"])]
    if summary:
        out.append(Paragraph(_esc(summary), st["body"]))
    out.append(Paragraph("Overview", st["h2"]))
    out.append(_kv_table([
        ("Score", f"{o.get('total', '?')}/{o.get('max_possible', '?')}  ({o.get('score_pct', '?')}%)"),
        ("Priority", o.get("priority_tier", "?")),
        ("Page type", o.get("page_type", "?")),
        ("Query intent", o.get("intent", "?")),
        ("Review", review),
    ], st, page_w))

    scores = sections.get("scores", []) or []
    if scores:
        out.append(Paragraph("Criterion scores (weakest first)", st["h2"]))
        data = [[Paragraph("<b>#</b>", st["cell"]), Paragraph("<b>Criterion</b>", st["cell"]),
                 Paragraph("<b>Score</b>", st["cell"]), Paragraph("<b>Evidence</b>", st["cell"])]]
        for s in scores:
            data.append([
                Paragraph("●" * int(s.get("value", 0)) + "○" * (5 - int(s.get("value", 0))), st["cell"]),
                Paragraph(_esc(s.get("criterion")), st["cell"]),
                Paragraph(f"{s.get('value')}/5", st["cell"]),
                Paragraph(_esc(s.get("notes", "")), st["cell"]),
            ])
        t = Table(data, colWidths=[page_w * 0.13, page_w * 0.27, page_w * 0.1, page_w * 0.5], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        out.append(t)

    recs = sections.get("recommendations", []) or []
    if recs:
        out.append(Paragraph("Recommendations (priority order)", st["h2"]))
        for i, r in enumerate(recs, start=1):
            out.append(Paragraph(f"<b>{i}. [{_esc(r.get('type'))}/{_esc(r.get('criterion'))}]</b> {_esc(r.get('title'))}", st["body"]))
            if r.get("rationale"):
                out.append(Paragraph(f"<i>why:</i> {_esc(r['rationale'])}", st["small"]))
            for edit in _rec_edits(r.get("detail", {}) or {}):
                out.append(Paragraph(f"• {_esc(edit)}", st["small"]))
    out.append(Spacer(1, 6))
    return out


def _rec_edits(detail: dict[str, Any]) -> list[str]:
    if isinstance(detail.get("edits"), list):
        return [str(e) for e in detail["edits"]]
    if detail.get("guidance"):
        return [str(detail["guidance"])]
    if detail.get("schema_type"):
        return [f"Add {detail['schema_type']} JSON-LD (ready-to-paste markup in payload)"]
    return []


def _doc(path: str, title: str):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(
        path, pagesize=letter, title=title,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    ), letter[0] - 1.4 * inch


def write_page_reports_pdf(rows: list[PageReport | dict[str, Any]], path: str) -> str:
    """Render one or many per-page reports to a PDF (one report per page)."""
    _require_reportlab()
    from reportlab.platypus import PageBreak

    st = _styles()
    doc, page_w = _doc(path, "AEO Per-Page Report")
    story: list = []
    for i, row in enumerate(rows):
        if i:
            story.append(PageBreak())
        story.extend(_page_report_flowables(row, st, page_w))
    doc.build(story or [_styles_placeholder(st)])
    return path


def _styles_placeholder(st):
    from reportlab.platypus import Paragraph
    return Paragraph("No reports for that scope.", st["body"])


def write_site_report_pdf(report: SiteReport | dict[str, Any], path: str) -> str:
    """Render the site-level report to a PDF."""
    _require_reportlab()
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if isinstance(report, SiteReport):
        summary, sections = report.summary, report.sections
    else:
        summary = report.get("summary") or ""
        sections = report.get("sections") or {}
    o = sections.get("overview", {}) or {}
    cov = sections.get("coverage_gaps", {}) or {}
    roll = sections.get("page_rollup", {}) or {}

    st = _styles()
    doc, page_w = _doc(path, "AEO Site Report")
    story = [Paragraph(f"Site AEO Report — {_esc(o.get('topic', '?'))} (blueprint v{_esc(o.get('blueprint_version', '?'))})", st["title"])]
    if summary:
        story.append(Paragraph(_esc(summary), st["body"]))

    story.append(Paragraph("Coverage", st["h2"]))
    story.append(_kv_table([
        ("Ideal pages", o.get("ideal_pages", "?")),
        ("Covered", f"{o.get('covered_pages', '?')}  ({o.get('coverage_pct', '?')}%)"),
        ("Missing", o.get("missing_pages", "?")),
        ("Pages analyzed", o.get("pages_analyzed", "?")),
    ], st, page_w))

    thin = cov.get("thin_clusters", []) or []
    if thin:
        story.append(Paragraph("Thin clusters", st["h2"]))
        for t in thin:
            story.append(Paragraph(f"• {_esc(t.get('cluster'))}: {t.get('present')}/{t.get('target')} (need {t.get('shortfall')} more)", st["small"]))

    briefs = cov.get("new_page_recommendations", []) or []
    if briefs:
        story.append(Paragraph("Net-new content (missing pages, priority order)", st["h2"]))
        data = [[Paragraph("<b>Priority</b>", st["cell"]), Paragraph("<b>Type</b>", st["cell"]),
                 Paragraph("<b>Slug</b>", st["cell"]), Paragraph("<b>Seed question</b>", st["cell"])]]
        for b in briefs[:30]:
            q = b["seed_questions"][0] if b.get("seed_questions") else ""
            data.append([Paragraph(_esc(b.get("priority")), st["cell"]), Paragraph(_esc(b.get("page_type")), st["cell"]),
                         Paragraph(_esc(b.get("slug")), st["cell"]), Paragraph(_esc(q), st["cell"])])
        t = Table(data, colWidths=[page_w * 0.12, page_w * 0.14, page_w * 0.3, page_w * 0.44], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(t)

    story.append(Paragraph("Page rollup", st["h2"]))
    story.append(_kv_table([
        ("Pages analyzed", f"{roll.get('pages', 0)}  (avg {roll.get('avg_score_pct', 0)}%)"),
        ("By priority", roll.get("by_priority", {})),
        ("By review", roll.get("by_review", {})),
    ], st, page_w))
    worst = roll.get("worst_pages", []) or []
    if worst:
        story.append(Paragraph("Worst pages", st["h2"]))
        for w in worst[:10]:
            story.append(Paragraph(f"• {w.get('total')}  {_esc(w.get('url'))}", st["small"]))

    story.append(Spacer(1, 6))
    doc.build(story)
    return path
