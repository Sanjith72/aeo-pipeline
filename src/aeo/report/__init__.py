"""
Report block — assemble and render the per-page AEO/SEO report (final deliverable).

  * :func:`build_report` — fold score + gap + recommendations + validation into a
    :class:`PageReport` (summary + sections JSONB + review_status).
  * :func:`persist_report` — upsert it into ``page_reports``.
  * :func:`render_report` — plain-text rendering for ``aeo report``.
"""

from __future__ import annotations

from .builder import (
    REVIEW_APPROVED,
    REVIEW_COULD_NOT_IMPROVE,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    PageReport,
    build_report,
    persist_report,
)
from .render import render_report
from .site_builder import (
    SiteReport,
    build_site_report,
    new_page_briefs,
    render_site_report,
)

__all__ = [
    "REVIEW_APPROVED",
    "REVIEW_COULD_NOT_IMPROVE",
    "REVIEW_PENDING",
    "REVIEW_REJECTED",
    "PageReport",
    "SiteReport",
    "build_report",
    "build_site_report",
    "new_page_briefs",
    "persist_report",
    "render_report",
    "render_site_report",
]
