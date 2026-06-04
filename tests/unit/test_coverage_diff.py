"""
Unit tests for the site-level Coverage Diff (v4), offline.

The diff is a deterministic spec: exact-slug and token-overlap matching, missing
nodes ordered by blueprint priority, and thin-cluster detection against the
authority target. Page-type guards stop a blog silently satisfying a product page.
"""

from __future__ import annotations

from aeo.processor.coverage_diff import DiscoveredPage, coverage_diff
from aeo.reference import Blueprint, CoverageCluster, CoverageMap, SitemapNode


def _bp() -> Blueprint:
    return Blueprint(
        topic="PEV",
        version=3,
        sitemap=[
            SitemapNode(slug="/what-is-ctem", title="CTEM", page_type="pillar", priority=0.9),
            SitemapNode(slug="/ctem-tools", title="Tools", page_type="solution", priority=0.8),
            SitemapNode(slug="/platform", title="Platform", page_type="product", priority=0.85),
            SitemapNode(slug="/what-is-remediation", title="RemOps", page_type="pillar", priority=0.7),
        ],
        coverage=CoverageMap(
            clusters=[
                CoverageCluster(name="ctem", pillar_slug="/what-is-ctem",
                                supporting_slugs=["/ctem-tools"], min_pages=10),
            ]
        ),
    )


class TestMatching:
    def test_exact_slug_match(self):
        d = [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")]
        res = coverage_diff(_bp(), d)
        assert "/what-is-ctem" in res.matched

    def test_token_overlap_match_same_page_type(self):
        # /guides/ctem-tools covers /ctem-tools (token overlap, same page_type solution)
        d = [DiscoveredPage.from_url("https://securin.io/guides/ctem-tools", "solution")]
        res = coverage_diff(_bp(), d)
        assert "/ctem-tools" in res.matched

    def test_page_type_mismatch_does_not_cover(self):
        # A blog about ctem tools must NOT satisfy the solution node.
        d = [DiscoveredPage.from_url("https://securin.io/blog/ctem-tools", "blog")]
        res = coverage_diff(_bp(), d)
        assert "/ctem-tools" not in res.matched

    def test_unrelated_page_covers_nothing(self):
        d = [DiscoveredPage.from_url("https://securin.io/careers", "about")]
        res = coverage_diff(_bp(), d)
        assert res.matched == []
        assert res.coverage_pct == 0.0


class TestMissingAndCoverage:
    def test_missing_nodes_listed_with_payload(self):
        d = [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")]
        res = coverage_diff(_bp(), d)
        missing_slugs = {m.slug for m in res.missing}
        assert "/platform" in missing_slugs and "/ctem-tools" in missing_slugs
        platform = next(m for m in res.missing if m.slug == "/platform")
        assert platform.page_type == "product"

    def test_coverage_pct(self):
        d = [
            DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar"),
            DiscoveredPage.from_url("https://securin.io/platform", "product"),
        ]
        res = coverage_diff(_bp(), d)
        assert res.matched_count == 2
        assert res.total_nodes == 4
        assert res.coverage_pct == 50.0

    def test_missing_ordered_by_priority(self):
        res = coverage_diff(_bp(), [])  # nothing discovered → all missing
        order = [m.slug for m in res.missing_by_priority()]
        # /what-is-ctem (0.9) > /platform (0.85) > /ctem-tools (0.8) > /what-is-remediation (0.7)
        assert order[0] == "/what-is-ctem"
        assert order[-1] == "/what-is-remediation"


class TestThinClusters:
    def test_cluster_below_min_pages_is_thin(self):
        d = [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")]
        res = coverage_diff(_bp(), d)
        ctem = next(t for t in res.thin_clusters if t.name == "ctem")
        assert ctem.present_count == 1
        assert ctem.min_pages == 10
        assert ctem.shortfall == 9
        assert "/ctem-tools" in ctem.missing_slugs

    def test_detail_round_trips(self):
        res = coverage_diff(_bp(), [])
        detail = res.to_detail()
        assert detail["topic"] == "PEV"
        assert detail["blueprint_version"] == 3
        assert len(detail["missing"]) == 4
        assert detail["coverage_pct"] == 0.0
