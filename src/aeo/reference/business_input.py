"""
Business brief — the SP-2 input for the no-website / pre-launch entry path.

Scenario 1 ("I want to rank in AI search") gives us a *business*, not a site: a name,
industry, location, the services offered, and some competitors. :class:`BusinessInput`
captures that brief so the pipeline can generate an ideal-site blueprint and a build plan
with **no crawl required** — see :func:`aeo.intelligence.brief.plan_from_brief`.

Pure data + two derivations (a stable config key and a topic hint). No I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .domain_config import normalize_domain

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class BusinessInput:
    """A business brief for the no-website blueprint path.

    ``domain`` is optional — a business that hasn't launched yet may only have a name.
    Everything else nudges the framework (``category``), the business-model classifier
    (``category`` / ``topic`` / industry text), and the deliverable framing (``goals``)."""

    name: str
    domain: str | None = None
    category: str | None = None
    topic: str | None = None
    location: str | None = None
    services: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)

    def key(self) -> str:
        """A stable config/framework key: the normalized domain when present, else a slug
        of the name (so a name-only brief still keys deterministically)."""
        if self.domain:
            host = normalize_domain(self.domain)
            if host:
                return host
        slug = _SLUG_RE.sub("-", self.name.strip().lower()).strip("-")
        return slug or "site"

    def topic_hint(self) -> str:
        return self.topic or self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "key": self.key(),
            "category": self.category,
            "topic": self.topic,
            "location": self.location,
            "services": self.services,
            "competitors": self.competitors,
            "goals": self.goals,
        }
