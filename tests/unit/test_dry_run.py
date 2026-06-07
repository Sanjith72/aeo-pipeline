"""Orchestrator.dry_run — in-memory preview that writes NOTHING to the DB.

Discovery and crawling are mocked, so the test is fully offline. The key guarantees:
a deterministic blueprint + site-level coverage diff are produced, optional per-page
scoring runs in memory, and no repo write is ever attempted (the method imports no
write path; mocking discover/fetch_many is sufficient to keep it offline)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from aeo.pipeline import orchestrator as orch_mod
from aeo.pipeline.orchestrator import Orchestrator
from aeo.storage.models import FetchedPage

_FIX = Path(__file__).resolve().parents[1] / "fixtures"


@dataclass
class _DUrl:
    url: str
    internal_links: int = 1


@dataclass
class _Discovery:
    urls: list
    source: str = "sitemap"


def _patch_discovery(monkeypatch, urls):
    async def fake_discover(domain, *, max_urls=None):
        return _Discovery(urls=[_DUrl(u, 3) for u in urls])

    monkeypatch.setattr(orch_mod, "discover", fake_discover)


def test_dry_run_structural_only_writes_nothing(monkeypatch):
    _patch_discovery(monkeypatch, [
        "https://securin.io/what-is-ctem",
        "https://securin.io/platform",
        "https://securin.io/blog/some-post",
    ])
    # Guard: if dry_run ever touched the DB, this would blow up rather than silently pass.
    import aeo.storage.repos.runs as runs_repo
    monkeypatch.setattr(runs_repo, "start", lambda *a, **k: pytest.fail("dry_run must not start a run"))

    result = asyncio.run(Orchestrator(llm=None).dry_run("securin.io", pages=0))

    assert result["mode"] == "dry-run"
    assert result["db_writes"] == 0
    assert result["discovered"] == 3
    assert result["topic"] == "PEV"
    assert result["engine_target"] == "perplexity"  # from config/domains/securin.io.yaml
    assert result["blueprint"]["nodes"] > 0
    assert result["blueprint"]["generator"] == "deterministic"
    assert 0.0 <= result["coverage"]["pct"] <= 100.0
    assert result["coverage"]["total_nodes"] == result["blueprint"]["nodes"]
    assert result["pages"] == []  # pages=0 → no crawl


def test_dry_run_scores_pages_in_memory(monkeypatch):
    _patch_discovery(monkeypatch, ["https://securin.io/what-is-ctem"])
    html = (_FIX / "strong_page.html").read_text(encoding="utf-8")

    async def fake_fetch_many(urls):
        return [
            FetchedPage(
                url=u, url_normalized=u, success=True, http_status=200,
                fetch_duration_ms=10, html=html, markdown="", title="t",
                meta_description="", error=None, content_hash="abc",
            )
            for u in urls
        ]

    monkeypatch.setattr(orch_mod, "fetch_many", fake_fetch_many)

    result = asyncio.run(Orchestrator(llm=None).dry_run("securin.io", pages=3))
    assert result["db_writes"] == 0
    assert len(result["pages"]) >= 1
    page = result["pages"][0]
    assert page["total"] > 0 and page["max_possible"] == 50
    assert page["priority_tier"]
