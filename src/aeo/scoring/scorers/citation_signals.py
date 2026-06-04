"""
Criterion 7 — Citation Signals (E-E-A-T).

Compound tiering: the page gets the highest tier whose author / date /
external-authority-link requirements are all satisfied. Author and date fall
back to JSON-LD when no on-page byline is found. The audit found Securin
pages have zero authority links and no bylines → tier 1.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("citation_signals")
    tiers: dict = crit.cfg.get("tiers", {})

    eeat = ctx.bundle.get("eeat", {}) or {}
    links = ctx.bundle.get("links", {}) or {}
    schema = ctx.bundle.get("schema_jsonld", {}) or {}

    has_author = bool(eeat.get("has_author")) or bool(schema.get("authors"))
    has_date = bool(eeat.get("has_date")) or bool(schema.get("dates"))
    authority = int(links.get("authority_count", 0) or 0)
    credentials = eeat.get("credentials_mentioned", [])

    value = 1
    for tier in sorted(tiers, key=lambda k: int(k), reverse=True):
        req = tiers[tier]
        if req.get("author") and not has_author:
            continue
        if req.get("date") and not has_date:
            continue
        if authority < int(req.get("authority_links", 0)):
            continue
        value = int(tier)
        break

    missing = []
    if not has_author:
        missing.append("no author")
    if not has_date:
        missing.append("no date")
    if authority == 0:
        missing.append("no authority links")

    return CriterionScore(
        name="citation_signals",
        value=value,
        evidence={
            "has_author": has_author,
            "author": eeat.get("author"),
            "has_date": has_date,
            "publish_date": eeat.get("publish_date"),
            "authority_link_count": authority,
            "authority_domains": links.get("authority_domains", []),
            "credentials_mentioned": credentials,
        },
        notes="; ".join(missing) or "author + date + authority links present",
    )
