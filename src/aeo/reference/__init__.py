"""Reference Layer (provisional) — best-practice targets, content architecture,
and query-intent classification. Consumers depend only on these accessors."""

from __future__ import annotations

from .blueprint import (
    GENERATOR_DETERMINISTIC,
    Blueprint,
    CoverageCluster,
    CoverageMap,
    SitemapNode,
    normalize_slug,
)
from .feedback import (
    CitationObservation,
    CriteriaRefinement,
    propose_criteria_refinements,
)
from .loader import (
    DEFAULT_TARGET,
    PageArchitecture,
    Reference,
    load_reference,
)
from .query_intent import QueryIntentCfg, classify_intent

__all__ = [
    "DEFAULT_TARGET",
    "GENERATOR_DETERMINISTIC",
    "Blueprint",
    "CitationObservation",
    "CoverageCluster",
    "CoverageMap",
    "CriteriaRefinement",
    "PageArchitecture",
    "QueryIntentCfg",
    "Reference",
    "SitemapNode",
    "classify_intent",
    "load_reference",
    "normalize_slug",
    "propose_criteria_refinements",
]
