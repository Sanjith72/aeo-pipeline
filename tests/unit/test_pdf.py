"""PDF export of the AEO deliverables. Skips if reportlab (the [pdf] extra) is absent."""

from __future__ import annotations

import pytest

pytest.importorskip("reportlab")

from aeo.report.pdf import write_page_reports_pdf, write_site_report_pdf


def _is_pdf(path) -> bool:
    data = path.read_bytes()
    return data[:5] == b"%PDF-" and b"%%EOF" in data[-4096:]


def test_page_reports_pdf(tmp_path):
    rows = [{
        "summary": "https://x/about scored 20/50 (40%).",
        "review_status": "pending",
        "sections": {
            "overview": {"url": "https://x/about", "total": 20, "max_possible": 50,
                         "score_pct": 40.0, "priority_tier": "high", "page_type": "about",
                         "intent": "navigational"},
            "scores": [
                {"criterion": "schema_markup", "value": 1, "notes": "No structured data found"},
                {"criterion": "load_speed", "value": 3, "notes": "PageSpeed unavailable — neutral"},
            ],
            "recommendations": [
                {"type": "schema", "criterion": "schema_markup", "title": "Add Organization JSON-LD",
                 "rationale": "primary entity unmarked", "detail": {"schema_type": "Organization"}},
            ],
        },
    }]
    out = tmp_path / "page.pdf"
    write_page_reports_pdf(rows, str(out))
    assert _is_pdf(out)


def test_multiple_page_reports_pdf(tmp_path):
    row = {"summary": "p", "review_status": "pending",
           "sections": {"overview": {"url": "u", "total": 30, "max_possible": 50}, "scores": []}}
    out = tmp_path / "multi.pdf"
    write_page_reports_pdf([row, row, row], str(out))
    assert _is_pdf(out)


def test_site_report_pdf(tmp_path):
    row = {
        "summary": "PEV: blueprint v1 — site covers 0/15 ideal pages (0.0%).",
        "sections": {
            "overview": {"topic": "PEV", "blueprint_version": 1, "coverage_pct": 0.0,
                         "ideal_pages": 15, "covered_pages": 0, "missing_pages": 15, "pages_analyzed": 11},
            "coverage_gaps": {
                "thin_clusters": [{"cluster": "ctem", "present": 0, "target": 10, "shortfall": 10}],
                "new_page_recommendations": [
                    {"slug": "/what-is-ctem", "page_type": "pillar", "priority": 0.9,
                     "seed_questions": ["What is CTEM?"]},
                ],
            },
            "page_rollup": {"pages": 11, "avg_score_pct": 51.3, "by_priority": {"high": 7},
                            "by_review": {"pending": 11}, "worst_pages": [{"total": 20, "url": "https://x/about"}]},
        },
    }
    out = tmp_path / "site.pdf"
    write_site_report_pdf(row, str(out))
    assert _is_pdf(out)
