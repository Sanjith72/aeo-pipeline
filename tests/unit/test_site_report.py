"""
Unit tests for the site-level report builder (v4), offline and pure.

Asserts the coverage rollup, the net-new content briefs (priority order), the
per-page distribution/worst-pages/review tally, and that rendering is stable.
"""

from __future__ import annotations

from aeo.processor.coverage_diff import DiscoveredPage, coverage_diff
from aeo.reference import Blueprint, CoverageCluster, CoverageMap, SitemapNode
from aeo.report.site_builder import build_site_report, new_page_briefs, render_site_report


def _blueprint() -> Blueprint:
    return Blueprint(
        topic="PEV",
        version=2,
        generator="deterministic",
        sitemap=[
            SitemapNode(slug="/what-is-ctem", title="CTEM", page_type="pillar", priority=0.9,
                        seed_questions=["What is CTEM?"]),
            SitemapNode(slug="/platform", title="Platform", page_type="product", priority=0.85),
            SitemapNode(slug="/ctem-tools", title="Tools", page_type="solution", priority=0.8),
        ],
        coverage=CoverageMap(
            clusters=[CoverageCluster(name="ctem", pillar_slug="/what-is-ctem",
                                      supporting_slugs=["/ctem-tools"], min_pages=10)]
        ),
    )


def _pages():
    return [
        {"url": "https://securin.io/what-is-ctem", "total": 40, "max_possible": 50,
         "priority_tier": "low", "review_status": "pending"},
        {"url": "https://securin.io/thin", "total": 18, "max_possible": 50,
         "priority_tier": "high", "review_status": "could_not_improve"},
    ]


class TestBuild:
    def test_overview_and_summary(self):
        bp = _blueprint()
        discovered = [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")]
        cov = coverage_diff(bp, discovered)
        rep = build_site_report(blueprint=bp, coverage=cov, pages=_pages(), run_id=5, target_id=1, blueprint_id=9)
        o = rep.sections["overview"]
        assert o["topic"] == "PEV"
        assert o["blueprint_version"] == 2
        assert o["covered_pages"] == 1
        assert o["ideal_pages"] == 3
        assert o["pages_analyzed"] == 2
        assert rep.run_id == 5 and rep.blueprint_id == 9
        assert "blueprint v2" in rep.summary

    def test_new_page_briefs_priority_order(self):
        bp = _blueprint()
        cov = coverage_diff(bp, [])  # everything missing
        briefs = new_page_briefs(cov)
        assert briefs[0]["slug"] == "/what-is-ctem"  # highest priority (0.9)
        assert briefs[0]["seed_questions"] == ["What is CTEM?"]

    def test_thin_cluster_in_coverage_gaps(self):
        bp = _blueprint()
        cov = coverage_diff(bp, [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")])
        rep = build_site_report(blueprint=bp, coverage=cov, pages=_pages(), run_id=1)
        thin = rep.sections["coverage_gaps"]["thin_clusters"]
        assert thin and thin[0]["cluster"] == "ctem"
        assert thin[0]["shortfall"] == 9

    def test_page_rollup_stats(self):
        bp = _blueprint()
        cov = coverage_diff(bp, [])
        rep = build_site_report(blueprint=bp, coverage=cov, pages=_pages(), run_id=1)
        roll = rep.sections["page_rollup"]
        assert roll["pages"] == 2
        assert roll["by_review"]["could_not_improve"] == 1
        assert roll["worst_pages"][0]["total"] == 18  # worst first


class TestRender:
    def test_render_contains_key_blocks(self):
        bp = _blueprint()
        cov = coverage_diff(bp, [])
        rep = build_site_report(blueprint=bp, coverage=cov, pages=_pages(), run_id=1)
        text = render_site_report(rep)
        assert "SITE AEO REPORT" in text
        assert "NET-NEW CONTENT" in text
        assert "PAGE ROLLUP" in text

    def test_render_accepts_db_row_dict(self):
        bp = _blueprint()
        cov = coverage_diff(bp, [])
        rep = build_site_report(blueprint=bp, coverage=cov, pages=[], run_id=1)
        row = {"summary": rep.summary, "sections": rep.sections}
        assert "SITE AEO REPORT" in render_site_report(row)
