"""Typed records moved across layers. Plain dataclasses; no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Target:
    id: int
    name: str
    domain: str
    kind: str  # 'client' | 'competitor'
    # Detected CMS for the site ('wordpress' | 'shopify' | 'unknown' | None). Only the
    # client-by-domain lookup that feeds the milestone dashboard hydrates this.
    cms_type: str | None = None


@dataclass(slots=True)
class CrawlRun:
    id: int
    run_key: str
    label: str | None
    started_at: datetime
    status: str


@dataclass(slots=True)
class FetchedPage:
    url: str
    url_normalized: str
    success: bool
    http_status: int | None
    fetch_duration_ms: int
    html: str
    markdown: str
    title: str
    meta_description: str
    error: str | None
    content_hash: str | None = None


@dataclass(slots=True)
class StoredPage:
    id: int
    url: str
    url_normalized: str
    content_hash: str | None
    crawl_status: str


@dataclass(slots=True)
class ExtractionBundle:
    page_id: int
    data: dict[str, Any] = field(default_factory=dict)

    def put(self, name: str, payload: dict[str, Any]) -> None:
        self.data[name] = payload

    def get(self, name: str, default: Any = None) -> Any:
        return self.data.get(name, default)


@dataclass(slots=True)
class CriterionScore:
    name: str
    value: int            # 1-5
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    scored_by: str = "deterministic"   # 'deterministic' | model name | 'hybrid'


@dataclass(slots=True)
class PageScore:
    page_id: int
    run_id: int
    criteria: dict[str, CriterionScore]
    total: int
    max_possible: int
    priority_tier: str
    rubric_version: str = "1.0"
