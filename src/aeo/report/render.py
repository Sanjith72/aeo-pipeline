"""
Plain-text rendering of a per-page report — what ``aeo report`` prints.

Pure: turns a :class:`PageReport` (or an equivalent ``page_reports`` DB row) into
a readable block. No colour codes so the output pipes/redirects cleanly; the CLI
adds emphasis around it.
"""

from __future__ import annotations

from typing import Any

from .builder import PageReport

_RULE = "=" * 72


def render_report(report: PageReport | dict[str, Any]) -> str:
    """Render one report. Accepts the dataclass or a DB row dict."""
    summary, sections, review = _unpack(report)
    overview = sections.get("overview", {}) or {}
    url = overview.get("url", "(unknown url)")

    lines: list[str] = [
        _RULE,
        f"AEO/SEO REPORT  -  {url}",
        _RULE,
        _wrap_summary(summary),
        "",
        _overview_block(overview),
        "",
        _scores_block(sections.get("scores", []) or []),
    ]

    gap = sections.get("gap")
    if gap:
        lines += ["", _gap_block(gap)]

    recs = sections.get("recommendations", []) or []
    if recs:
        lines += ["", _recs_block(recs)]

    validation = sections.get("validation")
    if validation:
        lines += ["", _validation_block(validation)]

    independent = sections.get("independent_validation")
    if independent:
        lines += ["", _independent_block(independent)]

    lines += ["", _review_block(sections.get("human_review", {}) or {}, review)]
    lines.append(_RULE)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unpack(report: PageReport | dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    if isinstance(report, PageReport):
        return report.summary, report.sections, report.review_status
    summary = report.get("summary") or ""
    sections = report.get("sections") or {}
    review = report.get("review_status") or sections.get("human_review", {}).get("status", "pending")
    return summary, sections, review


def _wrap_summary(summary: str, width: int = 72) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(summary, width=width)) if summary else ""


def _overview_block(o: dict[str, Any]) -> str:
    total, mx = o.get("total", "?"), o.get("max_possible", "?")
    bits = [
        "OVERVIEW",
        f"  Score        : {total}/{mx}  ({o.get('score_pct', '?')}%)",
        f"  Priority     : {o.get('priority_tier', '?')}",
    ]
    if o.get("page_type"):
        bits.append(f"  Page type    : {o['page_type']}")
    if o.get("intent"):
        bits.append(f"  Query intent : {o['intent']}")
    return "\n".join(bits)


def _scores_block(scores: list[dict[str, Any]]) -> str:
    bits = ["CRITERION SCORES (weakest first)"]
    for s in scores:
        bar = "*" * int(s.get("value", 0)) + "." * (5 - int(s.get("value", 0)))
        note = f"  {s.get('notes', '')}" if s.get("notes") else ""
        bits.append(f"  [{bar}] {s.get('value')}/5  {s.get('criterion'):22}{note}")
    return "\n".join(bits)


def _gap_block(gap: dict[str, Any]) -> str:
    comp = gap.get("competitor_gap")
    comp_txt = f"{comp}" if comp is not None else "n/a (no competitor)"
    bits = [
        "GAP ANALYSIS",
        f"  Best-practice gap : {gap.get('bestpractice_gap')}",
        f"  Competitor gap    : {comp_txt}",
        f"  Overall gap       : {gap.get('overall_gap')}",
    ]
    deficiencies = gap.get("deficiencies", []) or []
    if deficiencies:
        bits.append("  Deficiencies (priority order):")
        for d in deficiencies:
            comp_part = "" if d.get("competitor") is None else f", competitor {d['competitor']}"
            bits.append(
                f"    - {d.get('criterion'):22} {d.get('actual')} -> target {d.get('target')}"
                f"{comp_part}  (priority {d.get('priority')})"
            )
    return "\n".join(bits)


def _recs_block(recs: list[dict[str, Any]]) -> str:
    bits = ["RECOMMENDATIONS (priority order)"]
    for i, r in enumerate(recs, start=1):
        bits.append(f"  {i}. [{r.get('type')}/{r.get('criterion')}] {r.get('title')}")
        if r.get("rationale"):
            bits.append(f"     why: {r['rationale']}")
        for edit in _edit_lines(r.get("detail", {}) or {}):
            bits.append(f"     - {edit}")
    return "\n".join(bits)


def _edit_lines(detail: dict[str, Any]) -> list[str]:
    """Surface the concrete artifact of a recommendation for the reader."""
    if isinstance(detail.get("edits"), list):
        return [str(e) for e in detail["edits"]]
    if detail.get("guidance"):
        return [str(detail["guidance"])]
    if detail.get("schema_type"):
        return [f"Add {detail['schema_type']} JSON-LD (ready-to-paste markup in payload)"]
    return []


def _validation_block(v: dict[str, Any]) -> str:
    arrow = f"{v.get('score_before')} -> {v.get('score_after')}"
    delta = v.get("delta", 0)
    sign = f"+{delta}" if delta >= 0 else str(delta)
    return "\n".join(
        [
            "VALIDATION",
            f"  Outcome   : {v.get('status')} (after {v.get('attempts')} attempt(s))",
            f"  Re-score  : {arrow}  ({sign})",
        ]
    )


def _independent_block(v: dict[str, Any]) -> str:
    verdict = "PASS" if v.get("passed") else "FAIL"
    bits = ["INDEPENDENT VALIDATION (non-circular)", f"  Verdict : {verdict}"]
    for c in v.get("checks", []) or []:
        mark = "ok" if c.get("passed") else "X "
        bits.append(f"    [{mark}] {c.get('name'):22} {c.get('detail', '')}")
    cit = v.get("citation")
    if cit and cit.get("available"):
        bits.append(f"  Perplexity citation: {'CITED' if cit.get('cited') else 'not cited'}"
                    f" for {cit.get('question')!r}")
    return "\n".join(bits)


def _review_block(hr: dict[str, Any], review_status: str) -> str:
    status = hr.get("status", review_status)
    reason = hr.get("reason", "")
    flag = "ACTION REQUIRED" if hr.get("action_required") else "no action"
    return "\n".join(["HUMAN REVIEW", f"  Status : {status}  [{flag}]", f"  {reason}"])
