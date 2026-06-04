"""
Processor block — turns raw scores into actionable analysis.

Currently exposes the Dual-Layer Gap Analysis, which converts a page's
10-criterion :class:`~aeo.storage.models.PageScore` into a prioritized
deficiency list (best-practice gap + competitor gap). Feeds the Recommender.
"""

from __future__ import annotations

from .coverage_diff import (
    CoverageDiffResult,
    DiscoveredPage,
    MissingNode,
    ThinCluster,
    coverage_diff,
)
from .gap_analysis import (
    CompetitorPage,
    CriterionGap,
    GapResult,
    analyze_gap,
    persist_gap,
    select_competitor,
)

__all__ = [
    "CompetitorPage",
    "CoverageDiffResult",
    "CriterionGap",
    "DiscoveredPage",
    "GapResult",
    "MissingNode",
    "ThinCluster",
    "analyze_gap",
    "coverage_diff",
    "persist_gap",
    "select_competitor",
]
