"""
Scenario Router — the consultant's brain.

Given the classification (tier + structure), the business model, the journey
coverage, and the site-level Coverage Diff, this decides *which of the meeting's
scenarios the site is in* and produces a prioritized :class:`StrategyPlan`: a
headline, a narrative, the named deliverable to produce, and an ordered list of
concrete :class:`StrategyAction`s assembled from every engine's findings.

This is what turns "here's another report" into "here's what to do, in order":

  | tier              | scenario      | deliverable                    |
  | ----------------- | ------------- | ------------------------------ |
  | none              | no_website    | AEO Website Blueprint          |
  | single_page       | single_page   | Restructuring Roadmap          |
  | small             | small_site    | Gap Analysis & Build Plan      |
  | medium            | growing_site  | Gap Analysis & Authority Plan  |
  | large/enterprise  | mature_site   | Consolidation & Authority Plan |

`agency_mode` (a BusinessModel.AGENCY client, or an explicit flag) reframes the
narrative as a client-ready executive summary — Scenario 5 with no separate path.

Pure and deterministic: templates by default. (An optional LLM pass can later
rewrite the narrative into prose behind the same enrichment seam — out of scope here.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..processor.coverage_diff import CoverageDiffResult
from .business_intent import BusinessIntent, BusinessModel
from .classification import Classification, SiteClass
from .config import IntelligenceCfg, load_intelligence_cfg
from .journey import JourneyCoverage


class Scenario(StrEnum):
    NO_WEBSITE = "no_website"
    SINGLE_PAGE = "single_page"
    SMALL_SITE = "small_site"
    GROWING_SITE = "growing_site"
    MATURE_SITE = "mature_site"


@dataclass(slots=True)
class StrategyAction:
    title: str
    detail: str
    category: str  # structure | content | journey | authority | entity | schema | linking | consolidation
    effort: str = "medium"  # low | medium | high
    priority: int = 0  # 1 = highest, assigned by the prioritizer
    related_slugs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StrategyPlan:
    scenario: Scenario
    headline: str
    narrative: str
    deliverable: str
    actions: list[StrategyAction] = field(default_factory=list)
    agency_mode: bool = False


# How action categories are ordered per scenario (first = highest priority). Anything
# not listed sorts after the listed ones, in insertion order (stable sort).
_CATEGORY_ORDER: dict[Scenario, list[str]] = {
    Scenario.NO_WEBSITE:   ["structure", "content", "journey", "entity", "schema"],
    Scenario.SINGLE_PAGE:  ["structure", "content", "journey", "schema", "entity"],
    Scenario.SMALL_SITE:   ["content", "journey", "structure", "authority", "entity", "schema"],
    Scenario.GROWING_SITE: ["content", "authority", "journey", "entity", "linking", "schema"],
    Scenario.MATURE_SITE:  ["authority", "consolidation", "linking", "content", "schema", "journey"],
}
_DEFAULT_ORDER = ["structure", "content", "journey", "authority", "entity", "schema", "linking", "consolidation"]

_MAX_CONTENT_ACTIONS = 8
_LOW_COVERAGE_PCT = 60.0


def _scenario_for(site_class: SiteClass, cfg: IntelligenceCfg) -> Scenario:
    return Scenario(cfg.scenario_map.get(site_class.value, "small_site"))


def _structure_actions(classification: Classification) -> list[StrategyAction]:
    out: list[StrategyAction] = []
    for arche in classification.structure.missing_archetypes:
        label = arche.replace("_", " ")
        out.append(
            StrategyAction(
                title=f"Add a {label} page",
                detail=(
                    f"No {label} page was found. It is an expected part of a discoverable "
                    f"site for this business model and anchors both trust and internal linking."
                ),
                category="structure",
                effort="low",
            )
        )
    return out


def _content_actions(coverage: CoverageDiffResult | None) -> list[StrategyAction]:
    if coverage is None:
        return []
    out: list[StrategyAction] = []
    for node in coverage.missing_by_priority()[:_MAX_CONTENT_ACTIONS]:
        out.append(
            StrategyAction(
                title=f"Create: {node.title}",
                detail=node.rationale or f"Missing {node.page_type} page for the ideal architecture.",
                category="content",
                effort="medium",
                related_slugs=[node.slug],
            )
        )
    return out


def _journey_actions(journey: JourneyCoverage) -> list[StrategyAction]:
    out: list[StrategyAction] = []
    for stage in journey.gaps:
        fillers = journey.filling_nodes.get(stage.value, [])
        hint = f" Start with: {', '.join(fillers)}." if fillers else ""
        out.append(
            StrategyAction(
                title=f"Cover the {stage.value} stage of the journey",
                detail=(
                    f"No page serves the {stage.value} stage, so users at that point in their "
                    f"journey have nothing to land on.{hint}"
                ),
                category="journey",
                effort="medium",
                related_slugs=fillers,
            )
        )
    return out


def _authority_actions(coverage: CoverageDiffResult | None) -> list[StrategyAction]:
    if coverage is None:
        return []
    out: list[StrategyAction] = []
    for thin in coverage.thin_clusters:
        out.append(
            StrategyAction(
                title=f"Build topical authority in the {thin.name} cluster",
                detail=(
                    f"The {thin.name} cluster has {thin.present_count} of a target {thin.min_pages} "
                    f"pages — too thin to earn topical authority. Add {thin.shortfall} more."
                ),
                category="authority",
                effort="high",
                related_slugs=list(thin.missing_slugs[:_MAX_CONTENT_ACTIONS]),
            )
        )
    return out


def _coverage_actions(coverage: CoverageDiffResult | None) -> list[StrategyAction]:
    if coverage is None or coverage.coverage_pct >= _LOW_COVERAGE_PCT:
        return []
    return [
        StrategyAction(
            title="Raise entity & schema coverage to AEO-ready levels",
            detail=(
                f"Only {coverage.coverage_pct}% of the ideal architecture is covered. Add the "
                f"required entities and structured-data (JSON-LD) the answer engines look for "
                f"on the highest-priority pages."
            ),
            category="schema",
            effort="medium",
        )
    ]


def _consolidation_actions(classification: Classification, scenario: Scenario) -> list[StrategyAction]:
    if scenario is not Scenario.MATURE_SITE:
        return []
    dist = classification.structure.type_distribution
    biggest = max(dist.items(), key=lambda kv: kv[1], default=("content", 0))
    return [
        StrategyAction(
            title="Audit for overlapping / cannibalizing pages",
            detail=(
                f"At {classification.structure.page_count} pages (largest type: '{biggest[0]}' "
                f"with {biggest[1]}), the wins shift from adding pages to consolidating "
                f"overlapping ones and strengthening internal links between pillars and support."
            ),
            category="consolidation",
            effort="high",
        ),
        StrategyAction(
            title="Strengthen pillar→supporting internal linking",
            detail=(
                "Ensure every supporting page links up to its pillar and pillars cross-link "
                "related clusters, so authority flows and answer engines can map the topic graph."
            ),
            category="linking",
            effort="medium",
        ),
    ]


def _prioritize(actions: list[StrategyAction], scenario: Scenario) -> list[StrategyAction]:
    order = _CATEGORY_ORDER.get(scenario, _DEFAULT_ORDER)
    rank = {cat: i for i, cat in enumerate(order)}
    ordered = sorted(actions, key=lambda a: rank.get(a.category, len(order)))  # stable within category
    for i, action in enumerate(ordered, start=1):
        action.priority = i
    return ordered


def _headline(scenario: Scenario, classification: Classification, intent: BusinessIntent,
              coverage: CoverageDiffResult | None) -> str:
    n = classification.structure.page_count
    pct = coverage.coverage_pct if coverage is not None else 0.0
    if scenario is Scenario.NO_WEBSITE:
        return "No website yet — here's the complete site to build, page by page."
    if scenario is Scenario.SINGLE_PAGE:
        return "One page is holding you back — here's how to grow it into a site AI can recommend."
    if scenario is Scenario.SMALL_SITE:
        return f"Your {n}-page site has {pct}% of what AI assistants look for — here's how to close the gap."
    if scenario is Scenario.GROWING_SITE:
        return f"Your {n}-page site is growing — becoming the go-to name in your space is the next step ({pct}% there)."
    return f"Your {n}-page site has a strong footprint — the wins now come from tightening it, not adding more."


def _narrative(scenario: Scenario, classification: Classification, intent: BusinessIntent,
               journey: JourneyCoverage, coverage: CoverageDiffResult | None, agency_mode: bool) -> str:
    n = classification.structure.page_count
    model = intent.model.value.replace("_", " ")
    struct_pct = round(classification.structure.structure_score * 100)
    missing_arche = ", ".join(a.replace("_", " ") for a in classification.structure.missing_archetypes)
    gaps = ", ".join(g.value.replace("_", " ") for g in journey.gaps)
    cov_pct = coverage.coverage_pct if coverage is not None else 0.0
    missing_pages = len(coverage.missing) if coverage is not None else 0

    lead = (
        "Executive summary for the client: "
        if agency_mode
        else ""
    )
    if scenario is Scenario.NO_WEBSITE:
        body = (
            f"{lead}You run a {model} business without a website yet — which means AI assistants "
            "have nothing to point customers to when they ask. "
        )
    else:
        body = (
            f"{lead}You run a {model} business. We found {n} page(s) on your site today — "
            f"{struct_pct}% of the core pages customers expect are in place, covering {cov_pct}% "
            f"of what an ideal site in your space would offer. "
        )
    if missing_arche:
        body += f"Pages worth adding: {missing_arche}. "
    if gaps:
        body += f"Where customers lose track of you: {gaps}. "
    if scenario in (Scenario.NO_WEBSITE, Scenario.SINGLE_PAGE):
        body += (
            "First, get the core pages in place — until they exist, nothing else moves the "
            "needle. The plan below lists every page your site needs and the order to build them."
        )
    elif scenario in (Scenario.SMALL_SITE, Scenario.GROWING_SITE):
        body += (
            f"You have a foundation to build on. The fastest path is filling the {missing_pages} "
            f"missing page(s) above, in the order shown, until you're the obvious answer in your space."
        )
    else:
        body += (
            "Your site is established — adding more pages now helps less than tidying overlapping "
            "content, connecting related pages, and deepening the thin spots. That's where this plan focuses."
        )
    return body


def route_scenario(
    *,
    site_class: SiteClass,
    intent: BusinessIntent,
    journey: JourneyCoverage,
    coverage: CoverageDiffResult | None,
    classification: Classification,
    cfg: IntelligenceCfg | None = None,
) -> StrategyPlan:
    """Decide the scenario and assemble the prioritized strategy plan."""
    cfg = cfg or load_intelligence_cfg()
    scenario = _scenario_for(site_class, cfg)
    agency_mode = intent.model is BusinessModel.AGENCY

    actions = (
        _structure_actions(classification)
        + _content_actions(coverage)
        + _journey_actions(journey)
        + _authority_actions(coverage)
        + _coverage_actions(coverage)
        + _consolidation_actions(classification, scenario)
    )
    actions = _prioritize(actions, scenario)

    return StrategyPlan(
        scenario=scenario,
        headline=_headline(scenario, classification, intent, coverage),
        narrative=_narrative(scenario, classification, intent, journey, coverage, agency_mode),
        deliverable=cfg.deliverables.get(scenario.value, "AEO Strategy"),
        actions=actions,
        agency_mode=agency_mode,
    )
