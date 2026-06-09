"""
SiteProfile — the intelligence layer's single output.

Runs the four engines in dependency order (classification tier → business model →
structure → journey → scenario) and folds them into one JSONB-serializable object the
site report, the ``aeo profile`` CLI, the zero-DB ``dry_run`` preview, and (later) the
UI all consume. Pure except for the optional LLM tiebreak inside business-model
detection; identical output across runs with the LLM off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..nlp.llm import LLMClient
from ..processor.coverage_diff import CoverageDiffResult
from .business_intent import BusinessIntent, detect_business_model
from .classification import Classification, build_classification, classify_tier
from .config import IntelligenceCfg, load_intelligence_cfg
from .journey import JourneyCoverage, analyze_journey
from .scenario import StrategyPlan, route_scenario
from .signals import to_page_views


@dataclass(slots=True)
class SiteProfile:
    domain: str
    classification: Classification
    business_intent: BusinessIntent
    journey: JourneyCoverage
    strategy: StrategyPlan

    def headline(self) -> str:
        return self.strategy.headline

    def to_dict(self) -> dict[str, Any]:
        """Stable JSONB payload (enum values flattened to strings) for
        ``site_reports.sections["strategy"]`` and the API/UI."""
        s = self.strategy
        c = self.classification
        b = self.business_intent
        return {
            "domain": self.domain,
            "scenario": s.scenario.value,
            "deliverable": s.deliverable,
            "headline": s.headline,
            "narrative": s.narrative,
            "agency_mode": s.agency_mode,
            "classification": {
                "site_class": c.site_class.value,
                "page_count": c.structure.page_count,
                "structure_score": c.structure.structure_score,
                "type_distribution": c.structure.type_distribution,
                "present_archetypes": c.structure.present_archetypes,
                "missing_archetypes": c.structure.missing_archetypes,
            },
            "business_intent": {
                "model": b.model.value,
                "confidence": b.confidence,
                "decided_by": b.decided_by,
                "evidence": b.evidence,
                "scores": b.scores,
            },
            "journey": {
                "stages": [
                    {
                        "stage": sc.stage.value,
                        "present_count": sc.present_count,
                        "covered": sc.covered,
                        "examples": sc.examples,
                    }
                    for sc in self.journey.stages
                ],
                "gaps": [g.value for g in self.journey.gaps],
                "filling_nodes": self.journey.filling_nodes,
            },
            "actions": [
                {
                    "priority": a.priority,
                    "title": a.title,
                    "detail": a.detail,
                    "category": a.category,
                    "effort": a.effort,
                    "related_slugs": a.related_slugs,
                }
                for a in s.actions
            ],
        }


def build_site_profile(
    *,
    domain: str,
    discovered: list[Any],
    coverage: CoverageDiffResult | None = None,
    topic: str | None = None,
    category: str | None = None,
    llm: LLMClient | None = None,
    cfg: IntelligenceCfg | None = None,
) -> SiteProfile:
    """Build the full :class:`SiteProfile` from the discovered, prioritized inventory
    (``ScoredUrl``-like rows) and an optional in-memory Coverage Diff.

    ``llm`` is used only as a tiebreak in business-model detection; everything else is
    deterministic. ``topic`` / ``category`` (from the per-domain onboarding config) nudge
    the business-model classifier."""
    cfg = cfg or load_intelligence_cfg()
    pages = to_page_views(discovered)

    site_class = classify_tier(len(pages), cfg)
    intent = detect_business_model(
        pages, site_class=site_class, topic=topic, category=category, cfg=cfg, llm=llm
    )
    classification = build_classification(
        pages=pages, site_class=site_class, model=intent.model.value, cfg=cfg
    )
    journey = analyze_journey(pages, coverage=coverage, cfg=cfg)
    strategy = route_scenario(
        site_class=site_class, intent=intent, journey=journey,
        coverage=coverage, classification=classification, cfg=cfg,
    )
    return SiteProfile(
        domain=domain,
        classification=classification,
        business_intent=intent,
        journey=journey,
        strategy=strategy,
    )
