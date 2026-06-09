"""
Website Classification Engine — site *tier* and structural *quality*.

The product-review meeting's core point: **structure matters more than page count.**
A 200-page site with no information architecture is weak; a 1-page site has almost
zero discoverability. So this engine emits two things:

  * a coarse :class:`SiteClass` tier (NONE → ENTERPRISE) from the discovered page
    count — the routing key the Scenario Router branches on; and
  * a :class:`StructureProfile` of which essential page *archetypes* (about, contact,
    services, industries, faq, resources, case_studies, blog, product) the site has,
    relative to what its business model is expected to have — a `structure_score`
    that captures organization, not size.

Pure and deterministic: it reads :class:`PageView`s and the cached
:class:`IntelligenceCfg`; no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .config import IntelligenceCfg, load_intelligence_cfg
from .signals import PageView, token_hit


class SiteClass(StrEnum):
    NONE = "none"
    SINGLE_PAGE = "single_page"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"


@dataclass(slots=True)
class StructureProfile:
    page_count: int
    type_distribution: dict[str, int]
    present_archetypes: list[str]
    missing_archetypes: list[str]  # expected-for-model archetypes that are absent
    structure_score: float  # 0..1 = present∩expected / expected


@dataclass(slots=True)
class Classification:
    site_class: SiteClass
    structure: StructureProfile
    signals: dict[str, Any] = field(default_factory=dict)


def classify_tier(page_count: int, cfg: IntelligenceCfg | None = None) -> SiteClass:
    """Map a discovered page count to a tier. Thresholds are inclusive upper bounds."""
    cfg = cfg or load_intelligence_cfg()
    t = cfg.thresholds
    if page_count <= 0:
        return SiteClass.NONE
    if page_count <= int(t.get("single_page", 1)):
        return SiteClass.SINGLE_PAGE
    if page_count <= int(t.get("small", 10)):
        return SiteClass.SMALL
    if page_count <= int(t.get("medium", 50)):
        return SiteClass.MEDIUM
    if page_count <= int(t.get("large", 200)):
        return SiteClass.LARGE
    return SiteClass.ENTERPRISE


def detect_archetypes(pages: list[PageView], cfg: IntelligenceCfg | None = None) -> dict[str, bool]:
    """Which essential archetypes are present, by page_type membership or slug token."""
    cfg = cfg or load_intelligence_cfg()
    paths = [p.path for p in pages]
    ptypes = {p.page_type for p in pages}
    present: dict[str, bool] = {}
    for name, rule in cfg.archetypes.items():
        wanted_types = set(rule.get("page_types", []) or [])
        tokens = rule.get("slug_tokens", []) or []
        by_type = bool(wanted_types & ptypes)
        by_token = any(token_hit(path, tok) for path in paths for tok in tokens)
        present[name] = by_type or by_token
    return present


def type_distribution(pages: list[PageView]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pages:
        out[p.page_type] = out.get(p.page_type, 0) + 1
    return out


def build_classification(
    *,
    pages: list[PageView],
    site_class: SiteClass,
    model: str,
    cfg: IntelligenceCfg | None = None,
) -> Classification:
    """Assemble the full classification. ``model`` (a ``BusinessModel`` value string)
    selects the expected-archetype set that the structure score is measured against."""
    cfg = cfg or load_intelligence_cfg()
    present_map = detect_archetypes(pages, cfg)
    present = [name for name, ok in present_map.items() if ok]

    expected = cfg.expected_archetypes.get(model) or list(cfg.archetypes.keys())
    covered = [a for a in expected if present_map.get(a, False)]
    missing = [a for a in expected if not present_map.get(a, False)]
    score = round(len(covered) / len(expected), 3) if expected else 0.0

    return Classification(
        site_class=site_class,
        structure=StructureProfile(
            page_count=len(pages),
            type_distribution=type_distribution(pages),
            present_archetypes=present,
            missing_archetypes=missing,
            structure_score=score,
        ),
        signals={"archetypes_present": present_map, "expected_for_model": expected},
    )
