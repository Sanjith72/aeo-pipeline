"""
Page Prioritization — pick the URLs worth optimizing.

A crawl surfaces far more pages than the per-page pipeline should process. This
ranks them so the loop spends its budget on the highest-value pages:

  final_score = base_weight(page_type) * traffic_signal

where ``base_weight`` reflects how much a page-type is worth optimizing for AEO
(config) and ``traffic_signal`` is the internal-link count today (a GSC export
can replace it later). The list is ranked descending and the top-N are
``selected`` for processing; the full ranking is persisted for observability.

Pure functions over an explicit :class:`PrioritizationCfg` (mirrors the
query-intent classifier), so classification and ranking are trivially testable
and tunable from ``config/prioritization.yaml`` with no code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

from ..settings import load_yaml_file

# First match wins; chosen so early-path section markers classify correctly.
DEFAULT_PRECEDENCE = ("utility", "blog", "pillar", "solution", "product", "contact", "about")
DEFAULT_TOP_N = 30
DEFAULT_BASE_WEIGHT = 0.5


@dataclass(slots=True)
class PrioritizationCfg:
    base_weights: dict[str, float] = field(default_factory=dict)
    url_patterns: dict[str, list[str]] = field(default_factory=dict)
    precedence: tuple[str, ...] = DEFAULT_PRECEDENCE
    homepage_paths: list[str] = field(default_factory=list)
    top_n: int = DEFAULT_TOP_N
    min_traffic_signal: float = 1.0
    default_type: str = "default"

    def base_weight_for(self, page_type: str) -> float:
        if page_type in self.base_weights:
            return self.base_weights[page_type]
        return self.base_weights.get(self.default_type, DEFAULT_BASE_WEIGHT)


@dataclass(slots=True)
class PageInput:
    """One candidate URL with its traffic proxy (internal inbound link count)."""

    url: str
    internal_links: int = 0


@dataclass(slots=True)
class ScoredUrl:
    url: str
    page_type: str
    base_weight: float
    traffic_signal: float
    final_score: float
    rank: int = 0
    selected: bool = False


def _path(url: str) -> str:
    return (urlsplit(url).path or "/").lower()


def classify(url: str, cfg: PrioritizationCfg) -> str:
    """Page-type from the URL path. Homepage is detected first (bare domain or a
    configured homepage path), then config patterns in precedence order, else the
    default type."""
    path = _path(url)
    norm = path.rstrip("/") or "/"
    if norm == "/" or norm in cfg.homepage_paths:
        return "homepage"

    for page_type in cfg.precedence:
        for needle in cfg.url_patterns.get(page_type, []):
            if needle in path:
                return page_type
    return cfg.default_type


def rank(pages: list[PageInput], cfg: PrioritizationCfg) -> list[ScoredUrl]:
    """Score, sort (desc), and flag the top-N. Ties break by base_weight then URL
    so the order is stable and deterministic across runs."""
    scored: list[ScoredUrl] = []
    for page in pages:
        page_type = classify(page.url, cfg)
        base_weight = cfg.base_weight_for(page_type)
        traffic = max(cfg.min_traffic_signal, float(page.internal_links))
        scored.append(
            ScoredUrl(
                url=page.url,
                page_type=page_type,
                base_weight=base_weight,
                traffic_signal=traffic,
                final_score=round(base_weight * traffic, 3),
            )
        )

    scored.sort(key=lambda s: (-s.final_score, -s.base_weight, s.url))
    for i, s in enumerate(scored, start=1):
        s.rank = i
        s.selected = i <= cfg.top_n
    return scored


@lru_cache(maxsize=1)
def load_prioritization_cfg() -> PrioritizationCfg:
    raw = load_yaml_file("prioritization.yaml")
    base_weights = {k: float(v) for k, v in (raw.get("base_weights", {}) or {}).items()}
    url_patterns = {
        k: [str(x).lower() for x in (v or [])]
        for k, v in (raw.get("url_patterns", {}) or {}).items()
    }
    precedence_raw = raw.get("precedence")
    precedence = tuple(precedence_raw) if precedence_raw else DEFAULT_PRECEDENCE
    homepage_paths = [str(x).rstrip("/").lower() for x in (raw.get("homepage_paths", []) or [])]
    return PrioritizationCfg(
        base_weights=base_weights,
        url_patterns=url_patterns,
        precedence=precedence,
        homepage_paths=homepage_paths,
        top_n=int(raw.get("top_n", DEFAULT_TOP_N)),
        min_traffic_signal=float(raw.get("min_traffic_signal", 1.0)),
        default_type=str(raw.get("default_type", "default")),
    )


def prioritize(pages: list[PageInput], cfg: PrioritizationCfg | None = None) -> list[ScoredUrl]:
    """Convenience: classify + rank using the loaded config when none is given."""
    return rank(pages, cfg or load_prioritization_cfg())


def persist_ranking(run_id: int, scored: list[ScoredUrl]) -> None:
    """Write the full ranking to page_priorities (selected flag included)."""
    from ..storage.repos import priorities as priorities_repo

    for s in scored:
        priorities_repo.upsert(
            run_id,
            s.url,
            s.page_type,
            s.base_weight,
            s.traffic_signal,
            s.final_score,
            final_rank=s.rank,
            selected=s.selected,
        )
