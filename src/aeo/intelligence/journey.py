"""
Journey Coverage Engine — does the site serve the whole funnel?

The meeting asked the pipeline to think in user journeys, not just pages. This maps
the discovered pages onto a 5-stage funnel — **awareness → consideration → decision
→ conversion → retention** — and reports which stages have no page at all. For each
gap it pulls the missing blueprint nodes (from the Coverage Diff) whose slug pattern
serves that stage, so a gap arrives with concrete "build these" suggestions.

> The blueprint contract (`reference.blueprint.JourneyStage`) is a closed *3-value*
> Literal (awareness/consideration/decision) and stays that way — it is the contract.
> This engine defines its own richer *5-stage* :class:`Stage` enum (adds conversion,
> retention) for the funnel view, mapping discovered pages + missing nodes onto it via
> config (`journey.stage_signals`). The two vocabularies are deliberately separate.

Pure and deterministic: reads :class:`PageView`s + an optional
:class:`CoverageDiffResult`; no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..processor.coverage_diff import CoverageDiffResult
from .config import IntelligenceCfg, load_intelligence_cfg
from .signals import PageView, token_hit


class Stage(StrEnum):
    AWARENESS = "awareness"
    CONSIDERATION = "consideration"
    DECISION = "decision"
    CONVERSION = "conversion"
    RETENTION = "retention"


# Funnel order — also the order gaps are reported in.
FUNNEL_ORDER: tuple[Stage, ...] = (
    Stage.AWARENESS, Stage.CONSIDERATION, Stage.DECISION, Stage.CONVERSION, Stage.RETENTION,
)

_MAX_EXAMPLES = 5
_MAX_FILLERS = 5


@dataclass(slots=True)
class StageCoverage:
    stage: Stage
    present_count: int
    examples: list[str] = field(default_factory=list)
    covered: bool = False


@dataclass(slots=True)
class JourneyCoverage:
    stages: list[StageCoverage]
    gaps: list[Stage] = field(default_factory=list)
    filling_nodes: dict[str, list[str]] = field(default_factory=dict)  # stage value -> missing slugs


def _page_matches_stage(page: PageView, sig: dict[str, list[str]]) -> bool:
    if page.page_type in set(sig.get("page_types", []) or []):
        return True
    return any(token_hit(page.path, tok) for tok in (sig.get("slug_tokens", []) or []))


def _filling_nodes(
    gaps: list[Stage], coverage: CoverageDiffResult | None, cfg: IntelligenceCfg
) -> dict[str, list[str]]:
    """For each gap stage, the missing blueprint nodes that would serve it."""
    out: dict[str, list[str]] = {g.value: [] for g in gaps}
    if coverage is None:
        return out
    for g in gaps:
        sig = cfg.stage_signals.get(g.value, {})
        wanted_types = set(sig.get("page_types", []) or [])
        tokens = sig.get("slug_tokens", []) or []
        slugs: list[str] = []
        for node in coverage.missing:
            if node.page_type in wanted_types or any(token_hit(node.slug, tok) for tok in tokens):
                slugs.append(node.slug)
            if len(slugs) >= _MAX_FILLERS:
                break
        out[g.value] = slugs
    return out


def analyze_journey(
    pages: list[PageView],
    *,
    coverage: CoverageDiffResult | None = None,
    cfg: IntelligenceCfg | None = None,
) -> JourneyCoverage:
    """Map pages onto the 5-stage funnel and find uncovered stages."""
    cfg = cfg or load_intelligence_cfg()
    counts: dict[Stage, int] = {s: 0 for s in FUNNEL_ORDER}
    examples: dict[Stage, list[str]] = {s: [] for s in FUNNEL_ORDER}

    for page in pages:
        for stage in FUNNEL_ORDER:
            if _page_matches_stage(page, cfg.stage_signals.get(stage.value, {})):
                counts[stage] += 1
                if len(examples[stage]) < _MAX_EXAMPLES:
                    examples[stage].append(page.path)

    stages = [
        StageCoverage(stage=s, present_count=counts[s], examples=examples[s], covered=counts[s] > 0)
        for s in FUNNEL_ORDER
    ]
    gaps = [s for s in FUNNEL_ORDER if counts[s] == 0]
    return JourneyCoverage(stages=stages, gaps=gaps, filling_nodes=_filling_nodes(gaps, coverage, cfg))
