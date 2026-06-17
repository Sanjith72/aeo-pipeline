"""
Intake intelligence (#2, #3) — derive what we used to ASK, and branch on crawl quality.

The redesign makes the URL the only required input. Two jobs follow from that:

* **Infer, don't ask** — ``infer_industry`` / ``infer_location`` derive the industry
  and location from the discovered inventory + onboarding topic, so the user edits a
  prefilled value instead of typing it from scratch (the API still accepts an explicit
  override, which always wins).
* **Branch on quality** — ``classify_intake`` routes the run: a content-rich site is
  audited; a *thin* site (too few pages or too little text to audit meaningfully) goes
  to the brief/build path; a *dead*/unreachable site falls through to the no-website
  path in ``brief.py``.

Pure and dependency-light (a regex + the shared PageView). No DB, no network — every
function is unit-testable offline.
"""

from __future__ import annotations

import re
from typing import Any

from .config import IntelligenceCfg
from .signals import PageView

# Intake routes (what the crawl quality says we should do next).
RICH = "rich"   # enough content to run a comprehensive audit
THIN = "thin"   # some content, but too little to audit — brief/build path
DEAD = "dead"   # nothing crawlable — no-website path

# Coarse industry label per inferred business model — the fallback when neither an
# explicit category nor a specific topic is available.
_INDUSTRY_BY_MODEL: dict[str, str] = {
    "saas": "Software / SaaS",
    "ecommerce": "E-commerce / Retail",
    "local": "Local services",
    "agency": "Agency / Consultancy",
    "publisher": "Media / Publishing",
    "lead_gen": "Professional services",
    "enterprise": "Enterprise",
}

# A page that encodes a location in its path: /locations/austin, /location/new-york.
_LOCATION_PATH_RE = re.compile(r"/locations?/([a-z0-9][a-z0-9-]*)")
# Path segments that look like a location slug but aren't a place.
_LOCATION_STOPWORDS = frozenset({"index", "all", "map", "near-me", "near", "list", "find"})
# Topic values that carry no industry signal (don't echo them back as an industry).
_GENERIC_TOPICS = frozenset({"", "generic", "default", "unknown", "none"})

# Topic taxonomy KEY -> human display LABEL. The taxonomy key (e.g. "PEV") is what we
# measure against in framework.yaml; it must NOT leak to the UI as the industry. Keys are
# matched case-insensitively. A topic absent from this map is assumed to already be a
# human-readable industry (e.g. "Vulnerability Management") and passes through unchanged,
# preserving the prior behaviour. Mirror new entries in framework.yaml's `display_label`.
_TOPIC_DISPLAY_LABELS: dict[str, str] = {
    "pev": "Cybersecurity",  # securin.io: Proactive Exposure / Vulnerability management
}


def classify_intake(
    page_count: int,
    word_count: int | None,
    *,
    min_pages: int,
    min_words: int,
) -> str:
    """Route the run on crawl quality. ``page_count`` is the discovered/crawled page
    count; ``word_count`` is the total body word count when known (the live profile path
    doesn't fetch text, so it passes ``None`` and only the page count gates).

    Thin = below ``min_pages`` pages OR (when known) below ``min_words`` words. Zero
    pages = dead (the crawl found nothing → the no-website path)."""
    if page_count <= 0:
        return DEAD
    if page_count < max(1, min_pages):
        return THIN
    if word_count is not None and word_count < max(0, min_words):
        return THIN
    return RICH


def infer_industry(
    pages: list[PageView],
    *,
    topic: str | None = None,
    category: str | None = None,
    business_model: str | None = None,
    cfg: IntelligenceCfg | None = None,
) -> str | None:
    """Best-effort industry, derived deterministically. An explicit ``category`` (the
    user override) wins; then a specific crawl/onboarding ``topic`` (resolved through
    ``_TOPIC_DISPLAY_LABELS`` so a taxonomy key like ``"PEV"`` renders as its human label
    ``"Cybersecurity"`` rather than leaking the key); then a coarse label from the inferred
    ``business_model``. ``None`` only when nothing is known."""
    if category and category.strip():
        return category.strip()
    if topic and topic.strip().lower() not in _GENERIC_TOPICS:
        key = topic.strip()
        # Resolve a taxonomy key (e.g. "PEV") to its human label; an unmapped topic is
        # assumed already human-readable and echoed back unchanged.
        return _TOPIC_DISPLAY_LABELS.get(key.lower(), key)
    if business_model:
        return _INDUSTRY_BY_MODEL.get(business_model)
    return None


def infer_location(
    pages: list[PageView],
    *,
    cfg: IntelligenceCfg | None = None,
) -> str | None:
    """Best-effort location from the discovered paths. Honest by design: only returns a
    value when the site actually encodes one (a ``/locations/<place>`` slug), so a
    location-free site (e.g. a SaaS) correctly yields ``None`` rather than a guess."""
    for p in pages:
        m = _LOCATION_PATH_RE.search(p.path)
        if not m:
            continue
        seg = m.group(1)
        if seg in _LOCATION_STOPWORDS:
            continue
        return _humanize(seg)
    return None


def _humanize(segment: str) -> str:
    return segment.replace("-", " ").replace("_", " ").strip().title() or segment


def total_word_count(page_texts: list[Any]) -> int:
    """Sum the body word counts across crawled pages (for the thin-site gate when text
    is available). Accepts strings or anything str()-able; counts whitespace-split words."""
    total = 0
    for t in page_texts:
        if t:
            total += len(str(t).split())
    return total
