"""
Brief → Plan — the SP-2 no-website entry path.

Turns a :class:`BusinessInput` brief into a complete AEO plan **without a crawl**: build
the ideal-site blueprint from the brief-tailored framework, treat the site as empty (no
pages exist yet) so the Coverage Diff marks every ideal page missing, then route through
the SP-1 intelligence layer to the ``no_website`` scenario + a prioritized build plan.

This is Scenario 1 ("I want to rank in AI search", no site yet). Pure given a
:class:`Framework`; the optional LLM only enriches blueprint synthesis + the business-
model tiebreak. The CLI (``aeo plan``) builds the framework from the brief (deterministic
skeleton, LLM-tailored when enabled) and hands it here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..nlp.llm import LLMClient
from ..processor.coverage_diff import CoverageDiffResult, coverage_diff
from ..reference.blueprint import Blueprint
from ..reference.business_input import BusinessInput
from ..reference.competitor_patterns import CompetitorPatterns
from ..reference.framework import Framework
from ..reference.generator import generate_blueprint
from .config import IntelligenceCfg, load_intelligence_cfg
from .site_profile import SiteProfile, build_site_profile


@dataclass(slots=True)
class BriefPlan:
    """A no-website AEO plan: the ideal site to build + the routed strategy."""

    business: BusinessInput
    blueprint: Blueprint
    coverage: CoverageDiffResult  # everything missing (no pages exist yet)
    profile: SiteProfile

    def to_dict(self) -> dict[str, Any]:
        return {
            "business": self.business.to_dict(),
            "blueprint": {
                "topic": self.blueprint.topic,
                "version": self.blueprint.version,
                "generator": self.blueprint.generator,
                "ideal_pages": len(self.blueprint.sitemap),
                "sitemap": [
                    {
                        "slug": n.slug,
                        "title": n.title,
                        "page_type": n.page_type,
                        "intent": n.intent,
                        "journey_stage": n.journey_stage,
                        "cluster": n.cluster,
                        "priority": n.priority,
                        "required_entities": n.required_entities,
                        "seed_questions": n.seed_questions,
                    }
                    for n in self.blueprint.sitemap
                ],
            },
            "coverage": {
                "pct": self.coverage.coverage_pct,
                "total_nodes": self.coverage.total_nodes,
                "missing": len(self.coverage.missing),
            },
            "profile": self.profile.to_dict(),
        }


def plan_from_brief(
    brief: BusinessInput,
    *,
    framework: Framework,
    llm: LLMClient | None = None,
    engine_target: str = "generic",
    cfg: IntelligenceCfg | None = None,
) -> BriefPlan:
    """Build a no-crawl AEO plan from a business brief.

    Generates the blueprint from the (brief-tailored) ``framework``, diffs it against an
    empty site (every ideal page missing), and routes through the intelligence layer to
    the ``no_website`` scenario + prioritized build plan. Pure given the framework; the
    optional ``llm`` only enriches blueprint synthesis and the business-model tiebreak."""
    cfg = cfg or load_intelligence_cfg()
    topic = brief.topic or framework.topic or brief.topic_hint()

    blueprint = generate_blueprint(
        topic=topic,
        framework=framework,
        patterns=CompetitorPatterns(),  # no competitor crawl on the no-website path
        llm=llm,
        engine_target=engine_target,
    )
    coverage = coverage_diff(blueprint, [])  # no website yet → every ideal page is missing
    profile = build_site_profile(
        domain=brief.key(),
        discovered=[],
        coverage=coverage,
        topic=topic,
        category=brief.category,
        llm=llm,
        cfg=cfg,
    )
    return BriefPlan(business=brief, blueprint=blueprint, coverage=coverage, profile=profile)
