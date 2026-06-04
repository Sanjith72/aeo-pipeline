"""
Marketing-fluff detection.

Promotional density is a deterministic input to content depth (criterion 6):
a page padded with "industry-leading, best-in-class, cutting-edge" boilerplate
reads thin to an answer engine no matter its length. This is intentionally a
keyword/phrase heuristic — cheap, explainable, and good enough to flag the
worst offenders without an LLM call.
"""

from __future__ import annotations

import re
from typing import Any

from ..utils.text import word_count

# Phrases that signal promotional rather than substantive copy. Lower-cased;
# matched case-insensitively as whole phrases.
MARKETING_PHRASES: tuple[str, ...] = (
    "industry-leading", "industry leading", "best-in-class", "best in class",
    "cutting-edge", "cutting edge", "world-class", "world class",
    "state-of-the-art", "state of the art", "next-generation", "next generation",
    "seamless", "seamlessly", "robust", "powerful", "innovative", "innovation",
    "revolutionary", "game-changing", "game changer", "leverage", "synergy",
    "empower", "empowering", "unlock", "unleash", "transformative",
    "trusted by", "end-to-end", "turnkey", "one-stop", "holistic", "bespoke",
    "mission-critical", "unparalleled", "unmatched", "premier",
    "leading provider", "comprehensive suite", "drive value", "value-add",
    "best in the industry", "thought leader", "thought leadership",
)

_PATTERNS = [(p, re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE)) for p in MARKETING_PHRASES]

# A page is "promotional" if it crosses either bar.
_MIN_HITS = 5
_MIN_DENSITY_PER_1K = 6.0


def analyze(text: str) -> dict[str, Any]:
    wc = word_count(text)
    found: dict[str, int] = {}
    total = 0
    for phrase, pat in _PATTERNS:
        n = len(pat.findall(text))
        if n:
            found[phrase] = n
            total += n

    density = (total / wc * 1000) if wc else 0.0
    is_promotional = total >= _MIN_HITS or density >= _MIN_DENSITY_PER_1K

    return {
        "marketing_phrases": sorted(found),
        "marketing_hits": found,
        "marketing_count": total,
        "word_count": wc,
        "promotional_density_per_1k": round(density, 2),
        "is_promotional": is_promotional,
    }
