"""SP-1 wiring — the SiteProfile surfaces in the site report (build + render)."""

from __future__ import annotations

from types import SimpleNamespace

from aeo.intelligence import build_site_profile
from aeo.processor.coverage_diff import CoverageDiffResult, MissingNode
from aeo.reference.blueprint import Blueprint, SitemapNode
from aeo.report.site_builder import build_site_report, render_site_report


def _blueprint() -> Blueprint:
    return Blueprint(
        topic="CTEM",
        version=1,
        sitemap=[SitemapNode(slug="/what-is-ctem", title="What is CTEM", page_type="pillar")],
    ).with_hash()


def _coverage() -> CoverageDiffResult:
    return CoverageDiffResult(
        topic="CTEM", blueprint_version=1, total_nodes=2, matched=["/"],
        missing=[MissingNode(slug="/pricing", title="Pricing", page_type="product",
                             intent="commercial", journey_stage="decision", cluster=None, priority=0.9)],
        thin_clusters=[],
    )


def _profile_dict() -> dict:
    pages = [
        SimpleNamespace(url="https://acme.com/", page_type="homepage"),
        SimpleNamespace(url="https://acme.com/pricing", page_type="product"),
        SimpleNamespace(url="https://acme.com/blog/x", page_type="blog"),
    ]
    return build_site_profile(domain="acme.com", discovered=pages, coverage=_coverage(),
                              topic="saas").to_dict()


def test_build_site_report_includes_strategy_section() -> None:
    report = build_site_report(
        blueprint=_blueprint(), coverage=_coverage(), pages=[],
        run_id=1, site_profile=_profile_dict(),
    )
    assert "strategy" in report.sections
    assert report.sections["strategy"]["scenario"]
    # the consultant framing leaks into the one-line summary, too
    assert "Scenario:" in report.summary


def test_build_site_report_without_profile_is_unchanged() -> None:
    report = build_site_report(blueprint=_blueprint(), coverage=_coverage(), pages=[], run_id=1)
    assert "strategy" not in report.sections
    assert "Scenario:" not in report.summary


def test_render_site_report_prints_strategy_block() -> None:
    report = build_site_report(
        blueprint=_blueprint(), coverage=_coverage(), pages=[],
        run_id=1, site_profile=_profile_dict(),
    )
    text = render_site_report(report)
    assert "STRATEGY" in text
    assert "Scenario" in text
    assert "ACTION PLAN" not in text or "Action plan" in text  # block label present when actions exist
    assert "Business model" in text
