"""Pipeline: crawl -> extract -> score -> persist, then Gap -> Validate -> Report,
plus the queue worker."""

from .analysis import (
    AnalysisResult,
    analyze_page,
    build_competitor_pool,
    is_could_not_improve,
    is_improved,
)
from .orchestrator import AnalysisSummary, Orchestrator, RunSummary
from .stages import ExtractStage, PersistStage, ScoreStage
from .worker import ANALYZE_RUN, CRAWL_BATCH, Worker, enqueue_analysis, enqueue_batch

__all__ = [
    "ANALYZE_RUN",
    "CRAWL_BATCH",
    "AnalysisResult",
    "AnalysisSummary",
    "ExtractStage",
    "Orchestrator",
    "PersistStage",
    "RunSummary",
    "ScoreStage",
    "Worker",
    "analyze_page",
    "build_competitor_pool",
    "enqueue_analysis",
    "enqueue_batch",
    "is_could_not_improve",
    "is_improved",
]
