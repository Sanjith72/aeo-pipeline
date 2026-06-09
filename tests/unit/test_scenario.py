"""Scenario Router — routing matrix, action assembly, prioritization, agency overlay."""

from __future__ import annotations

import pytest

from aeo.intelligence.business_intent import BusinessIntent, BusinessModel
from aeo.intelligence.classification import Classification, SiteClass, StructureProfile
from aeo.intelligence.journey import JourneyCoverage, Stage, StageCoverage
from aeo.intelligence.scenario import Scenario, route_scenario
from aeo.processor.coverage_diff import CoverageDiffResult, MissingNode, ThinCluster


def _classification(site_class: SiteClass, *, missing_archetypes=None, page_count=5) -> Classification:
    return Classification(
        site_class=site_class,
        structure=StructureProfile(
            page_count=page_count,
            type_distribution={"blog": page_count},
            present_archetypes=["about"],
            missing_archetypes=list(missing_archetypes or []),
            structure_score=0.5,
        ),
    )


def _intent(model: BusinessModel = BusinessModel.LEAD_GEN) -> BusinessIntent:
    return BusinessIntent(model=model, confidence=0.5, evidence=[], scores={}, decided_by="deterministic")


def _journey(gaps=None) -> JourneyCoverage:
    gaps = gaps or []
    stages = [StageCoverage(stage=s, present_count=0, examples=[], covered=s not in gaps) for s in Stage]
    return JourneyCoverage(stages=stages, gaps=list(gaps), filling_nodes={g.value: [] for g in gaps})


@pytest.mark.parametrize(
    ("site_class", "scenario", "deliverable"),
    [
        (SiteClass.NONE, Scenario.NO_WEBSITE, "AEO Website Blueprint"),
        (SiteClass.SINGLE_PAGE, Scenario.SINGLE_PAGE, "Restructuring Roadmap"),
        (SiteClass.SMALL, Scenario.SMALL_SITE, "Gap Analysis & Build Plan"),
        (SiteClass.MEDIUM, Scenario.GROWING_SITE, "Gap Analysis & Authority Plan"),
        (SiteClass.LARGE, Scenario.MATURE_SITE, "Consolidation & Authority Plan"),
        (SiteClass.ENTERPRISE, Scenario.MATURE_SITE, "Consolidation & Authority Plan"),
    ],
)
def test_routing_matrix(site_class: SiteClass, scenario: Scenario, deliverable: str) -> None:
    plan = route_scenario(
        site_class=site_class, intent=_intent(), journey=_journey(),
        coverage=None, classification=_classification(site_class), cfg=None,
    )
    assert plan.scenario is scenario
    assert plan.deliverable == deliverable


def test_structure_actions_from_missing_archetypes() -> None:
    plan = route_scenario(
        site_class=SiteClass.SINGLE_PAGE, intent=_intent(), journey=_journey(),
        coverage=None,
        classification=_classification(SiteClass.SINGLE_PAGE, missing_archetypes=["about", "contact", "services"]),
    )
    titles = [a.title for a in plan.actions if a.category == "structure"]
    assert any("about" in t for t in titles)
    assert any("contact" in t for t in titles)
    assert len(titles) == 3


def test_content_and_authority_actions_from_coverage() -> None:
    coverage = CoverageDiffResult(
        topic="t", blueprint_version=1, total_nodes=4, matched=[],
        missing=[
            MissingNode(slug="/pricing", title="Pricing", page_type="product", intent="commercial",
                        journey_stage="decision", cluster="c", priority=0.9, rationale="need pricing"),
        ],
        thin_clusters=[ThinCluster(name="ctem", present_count=1, min_pages=5, missing_slugs=["/a", "/b"])],
    )
    plan = route_scenario(
        site_class=SiteClass.MEDIUM, intent=_intent(), journey=_journey(),
        coverage=coverage, classification=_classification(SiteClass.MEDIUM),
    )
    cats = {a.category for a in plan.actions}
    assert "content" in cats
    assert "authority" in cats
    content = next(a for a in plan.actions if a.category == "content")
    assert content.related_slugs == ["/pricing"]


def test_journey_actions_from_gaps() -> None:
    plan = route_scenario(
        site_class=SiteClass.SMALL, intent=_intent(), journey=_journey(gaps=[Stage.CONVERSION]),
        coverage=None, classification=_classification(SiteClass.SMALL),
    )
    journey_actions = [a for a in plan.actions if a.category == "journey"]
    assert len(journey_actions) == 1
    assert "conversion" in journey_actions[0].title


def test_priorities_are_contiguous_and_ordered_by_category() -> None:
    # single_page prioritizes structure before journey.
    plan = route_scenario(
        site_class=SiteClass.SINGLE_PAGE, intent=_intent(), journey=_journey(gaps=[Stage.DECISION]),
        coverage=None,
        classification=_classification(SiteClass.SINGLE_PAGE, missing_archetypes=["about"]),
    )
    priorities = [a.priority for a in plan.actions]
    assert priorities == list(range(1, len(plan.actions) + 1))
    structure_idx = next(i for i, a in enumerate(plan.actions) if a.category == "structure")
    journey_idx = next(i for i, a in enumerate(plan.actions) if a.category == "journey")
    assert structure_idx < journey_idx


def test_mature_site_gets_consolidation_and_linking() -> None:
    plan = route_scenario(
        site_class=SiteClass.ENTERPRISE, intent=_intent(), journey=_journey(),
        coverage=None, classification=_classification(SiteClass.ENTERPRISE, page_count=400),
    )
    cats = {a.category for a in plan.actions}
    assert "consolidation" in cats
    assert "linking" in cats


def test_agency_mode_overlay() -> None:
    plan = route_scenario(
        site_class=SiteClass.MEDIUM, intent=_intent(BusinessModel.AGENCY), journey=_journey(),
        coverage=None, classification=_classification(SiteClass.MEDIUM),
    )
    assert plan.agency_mode is True
    assert plan.narrative.lower().startswith("executive summary")
