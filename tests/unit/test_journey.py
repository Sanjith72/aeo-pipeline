"""Journey Coverage Engine — 5-stage funnel mapping + gap → filling-node hints."""

from __future__ import annotations

from aeo.intelligence.journey import Stage, analyze_journey
from aeo.intelligence.signals import page_view
from aeo.processor.coverage_diff import CoverageDiffResult, MissingNode


def _missing(slug: str, page_type: str, journey_stage: str = "awareness") -> MissingNode:
    return MissingNode(
        slug=slug, title=slug.strip("/").title() or "Home", page_type=page_type,
        intent="informational", journey_stage=journey_stage, cluster=None, priority=0.7,
    )


def test_full_funnel_has_no_gaps() -> None:
    pages = [
        page_view("https://x.com/blog/intro", "blog"),  # awareness
        page_view("https://x.com/solutions/vs-acme", "solution"),  # consideration
        page_view("https://x.com/pricing", "product"),  # decision
        page_view("https://x.com/contact", "contact"),  # conversion
        page_view("https://x.com/support", "default"),  # retention
    ]
    jc = analyze_journey(pages)
    assert jc.gaps == []
    assert all(s.covered for s in jc.stages)


def test_awareness_only_site_has_four_gaps() -> None:
    # Two blog pages map to awareness ONLY (a 'pillar' would also serve consideration).
    pages = [page_view("https://x.com/blog/a", "blog"), page_view("https://x.com/blog/b", "blog")]
    jc = analyze_journey(pages)
    assert Stage.AWARENESS not in jc.gaps
    assert set(jc.gaps) == {Stage.CONSIDERATION, Stage.DECISION, Stage.CONVERSION, Stage.RETENTION}
    # gaps reported in funnel order
    assert jc.gaps == [Stage.CONSIDERATION, Stage.DECISION, Stage.CONVERSION, Stage.RETENTION]


def test_filling_nodes_from_coverage_diff() -> None:
    pages = [page_view("https://x.com/blog/a", "blog")]  # only awareness
    coverage = CoverageDiffResult(
        topic="t", blueprint_version=1, total_nodes=3,
        matched=["/blog"],
        missing=[
            _missing("/pricing", "product", "decision"),
            _missing("/contact", "contact", "decision"),
        ],
        thin_clusters=[],
    )
    jc = analyze_journey(pages, coverage=coverage)
    assert "/pricing" in jc.filling_nodes["decision"]
    assert "/contact" in jc.filling_nodes["conversion"]  # /contact token serves conversion


def test_no_coverage_yields_empty_fillers() -> None:
    pages = [page_view("https://x.com/blog/a", "blog")]
    jc = analyze_journey(pages, coverage=None)
    for gap in jc.gaps:
        assert jc.filling_nodes[gap.value] == []


def test_examples_are_bounded() -> None:
    pages = [page_view(f"https://x.com/blog/{i}", "blog") for i in range(20)]
    jc = analyze_journey(pages)
    awareness = next(s for s in jc.stages if s.stage is Stage.AWARENESS)
    assert awareness.present_count == 20
    assert len(awareness.examples) <= 5
