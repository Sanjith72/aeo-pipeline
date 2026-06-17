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

from .config import IntelligenceCfg, load_intelligence_cfg
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


def _looks_like_topic_code(value: str) -> bool:
    """A bare taxonomy code — e.g. "PEV", "CTEM", "PV" — vs. a human phrase. Codes are
    short, all-uppercase, and spaceless; they are internal routing keys, never a
    user-facing industry, so we must not surface them as one."""
    return value.isupper() and " " not in value and len(value) <= 8


def _resolve_industry_label(value: str, cfg: IntelligenceCfg) -> str | None:
    """Turn a raw topic/category value into a user-facing industry, or ``None`` when it
    isn't usable as one. Maps internal codes to human labels (``topic_industries``); keeps
    genuine human phrases; rejects unmapped codes and generic placeholders — so a routing
    key like "PEV"/"PV" can never reach the Industry field, no matter which branch it
    arrives on (this guard applies to the user/brief ``category`` too, not just ``topic``)."""
    v = value.strip()
    if not v or v.lower() in _GENERIC_TOPICS:
        return None
    mapped = cfg.topic_industries.get(v.lower())
    if mapped:
        return mapped
    if _looks_like_topic_code(v):
        return None  # an unmapped internal code — not a human industry
    return v


def infer_industry(
    pages: list[PageView],
    *,
    topic: str | None = None,
    category: str | None = None,
    business_model: str | None = None,
    cfg: IntelligenceCfg | None = None,
) -> str | None:
    """Best-effort industry, derived deterministically. An explicit ``category`` wins, then
    the ``topic`` — both run through :func:`_resolve_industry_label` so an internal
    taxonomy code (e.g. "PEV" → "Cyber Security") is mapped, a human phrase is kept, and an
    unmapped code is SKIPPED (never surfaced); then a coarse ``business_model`` label.
    ``None`` only when nothing usable is known."""
    cfg = cfg or load_intelligence_cfg()
    for value in (category, topic):
        if value:
            label = _resolve_industry_label(value, cfg)
            if label:
                return label
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
