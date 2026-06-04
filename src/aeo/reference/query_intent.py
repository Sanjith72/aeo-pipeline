"""
Query-intent classifier (CONCEPTUAL — lightweight URL + heading heuristic).

Pure function over an explicit :class:`QueryIntentCfg`, so it is trivially
testable and the heuristic can be tuned in ``config/best_practices.yaml`` with
no code change. Precedence is fixed: when a page matches several intents the
most business-valuable signal wins — ``commercial`` > ``navigational`` >
``informational``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Precedence order: first match wins.
PRECEDENCE = ("commercial", "navigational", "informational")


@dataclass(slots=True)
class QueryIntentCfg:
    default: str = "informational"
    # intent -> URL substrings (lowercased match against the full URL)
    url_patterns: dict[str, list[str]] = field(default_factory=dict)
    # intent -> heading keyword phrases (lowercased substring match)
    heading_keywords: dict[str, list[str]] = field(default_factory=dict)


def classify_intent(
    url: str,
    headings: list[str] | None,
    cfg: QueryIntentCfg,
) -> str:
    """Return the page's query intent. URL signals first, then headings, else default."""
    haystack_url = (url or "").lower()
    for intent in PRECEDENCE:
        for needle in cfg.url_patterns.get(intent, []):
            if needle.lower() in haystack_url:
                return intent

    if headings:
        haystack_head = " ".join(headings).lower()
        for intent in PRECEDENCE:
            for needle in cfg.heading_keywords.get(intent, []):
                if needle.lower() in haystack_head:
                    return intent

    return cfg.default
