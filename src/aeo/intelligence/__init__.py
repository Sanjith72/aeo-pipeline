"""
Intelligence layer — the consultant's brain on top of the page-and-rubric pipeline.

Turns the discovered, prioritized inventory + the site-level Coverage Diff into a
:class:`SiteProfile`: *who* the user is (business model), *how big/organized* their
site is (classification + structure), *which journey stages* are missing, and
therefore *what to do* (a prioritized, scenario-routed strategy plan). Deterministic
-first — every engine answers with the LLM off; the LLM only breaks business-model
ties.

Public surface is :func:`build_site_profile` + the result types; consumers (orchestrator,
CLI, site report) depend only on these.
"""

from __future__ import annotations

from .brief import BriefPlan, plan_from_brief
from .business_intent import BusinessIntent, BusinessModel, detect_business_model
from .classification import (
    Classification,
    SiteClass,
    StructureProfile,
    build_classification,
    classify_tier,
    detect_archetypes,
)
from .config import IntelligenceCfg, load_intelligence_cfg
from .journey import JourneyCoverage, Stage, StageCoverage, analyze_journey
from .scenario import Scenario, StrategyAction, StrategyPlan, route_scenario
from .signals import PageView, to_page_views
from .site_profile import SiteProfile, build_site_profile

__all__ = [
    "BriefPlan",
    "BusinessIntent",
    "BusinessModel",
    "Classification",
    "IntelligenceCfg",
    "JourneyCoverage",
    "PageView",
    "Scenario",
    "SiteClass",
    "SiteProfile",
    "Stage",
    "StageCoverage",
    "StrategyAction",
    "StrategyPlan",
    "StructureProfile",
    "analyze_journey",
    "build_classification",
    "build_site_profile",
    "classify_tier",
    "detect_archetypes",
    "detect_business_model",
    "load_intelligence_cfg",
    "plan_from_brief",
    "route_scenario",
    "to_page_views",
]
