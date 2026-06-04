"""
Reference Layer loader — typed accessors over config/best_practices.yaml.

This is the single seam the Processor (gap analysis) and Recommender depend on.
A richer future implementation (vector-backed, per-vertical, GSC-informed) only
needs to keep these accessors stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from ..settings import load_yaml_file
from .query_intent import QueryIntentCfg, classify_intent

# Mid-scale fallback for a criterion missing a configured target.
DEFAULT_TARGET = 3


@dataclass(slots=True)
class PageArchitecture:
    page_type: str
    must_have: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    target_word_count: int = 0


@dataclass(slots=True)
class Reference:
    targets: dict[str, int]
    architecture: dict[str, PageArchitecture]
    intent: QueryIntentCfg

    def target_for(self, criterion: str) -> int:
        """Best-practice target score (1-5) for a criterion; mid-scale default."""
        return self.targets.get(criterion, DEFAULT_TARGET)

    def architecture_for(self, page_type: str) -> PageArchitecture:
        """Ideal structure for a page-type, falling back to the 'default' entry."""
        return self.architecture.get(page_type, self.architecture["default"])

    def classify_intent(self, url: str, headings: list[str] | None = None) -> str:
        """Query intent for a page (delegates to the pure classifier)."""
        return classify_intent(url, headings, self.intent)


def _arch(page_type: str, cfg: dict) -> PageArchitecture:
    return PageArchitecture(
        page_type=page_type,
        must_have=list(cfg.get("must_have", []) or []),
        headings=list(cfg.get("headings", []) or []),
        target_word_count=int(cfg.get("target_word_count", 0) or 0),
    )


@lru_cache(maxsize=1)
def load_reference() -> Reference:
    raw = load_yaml_file("best_practices.yaml")

    targets = {k: int(v) for k, v in (raw.get("targets", {}) or {}).items()}

    arch_raw = raw.get("architecture", {}) or {}
    architecture = {pt: _arch(pt, cfg or {}) for pt, cfg in arch_raw.items()}
    architecture.setdefault("default", _arch("default", {}))

    qi = raw.get("query_intent", {}) or {}
    intent = QueryIntentCfg(
        default=qi.get("default", "informational"),
        url_patterns={k: list(v or []) for k, v in (qi.get("url_patterns", {}) or {}).items()},
        heading_keywords={k: list(v or []) for k, v in (qi.get("heading_keywords", {}) or {}).items()},
    )

    return Reference(targets=targets, architecture=architecture, intent=intent)
