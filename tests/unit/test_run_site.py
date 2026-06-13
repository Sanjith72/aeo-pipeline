"""
Orchestrator.run_site wiring — Site Discovery → Page Prioritization → crawl.

Asserts the integration logic only (no DB, no network, no browser): discovery and
the per-page crawl are replaced with fakes so the test pins the contract that
``run_site`` ranks the discovered inventory, persists the *full* ranking, and
crawls *only* the selected top-N.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from aeo.crawl.discovery import DiscoveredUrl, DiscoveryResult
from aeo.crawl.prioritize import PrioritizationCfg
from aeo.pipeline import orchestrator as orch_mod
from aeo.pipeline.orchestrator import Orchestrator, RunSummary
from aeo.storage.models import CrawlRun, Target

# top_n = 2 so the selection cut is observable with a small inventory.
CFG = PrioritizationCfg(
    base_weights={"homepage": 0.7, "blog": 0.8, "product": 0.9, "default": 0.5},
    url_patterns={"blog": ["/blog"], "product": ["/product"]},
    precedence=("blog", "product"),
    homepage_paths=[],
    top_n=2,
    min_traffic_signal=1.0,
    default_type="default",
)

TARGET = Target(id=1, name="Securin", domain="securin.io", kind="client")


@pytest.fixture
def wired(monkeypatch):
    """Patch out discovery, config, run creation, persistence, and the crawl;
    return a dict the test reads to see what run_site did."""
    captured: dict = {"persisted": None, "crawled": None}

    async def fake_discover(domain, *, fetch=None, max_urls=None, prefer_recursive=False):
        return DiscoveryResult(
            domain="https://x.com/", source="sitemap",
            urls=[
                DiscoveredUrl("https://x.com/", 20),          # homepage 0.7*20 = 14
                DiscoveredUrl("https://x.com/product/p", 10),  # product  0.9*10 = 9
                DiscoveredUrl("https://x.com/blog/a", 8),      # blog     0.8*8  = 6.4
                DiscoveredUrl("https://x.com/blog/b", 1),      # blog     0.8*1  = 0.8
            ],
        )

    def fake_start(label=None, run_key=None):
        captured["label"] = label
        return CrawlRun(id=42, run_key="run_test", label=label,
                        started_at=datetime(2026, 6, 1), status="running")

    def fake_persist(run_id, scored):
        captured["persisted"] = [(s.url, s.rank, s.selected) for s in scored]

    async def fake_run_pages(self, urls, *, run, target, do_score, progress=None):
        captured["crawled"] = list(urls)
        captured["do_score"] = do_score
        captured["progress"] = progress
        return RunSummary(run_id=run.id, run_key=run.run_key, total=len(urls), scored=len(urls))

    monkeypatch.setattr(orch_mod, "discover", fake_discover)
    monkeypatch.setattr(orch_mod, "load_prioritization_cfg", lambda: CFG)
    monkeypatch.setattr(orch_mod.runs_repo, "start", fake_start)
    monkeypatch.setattr(orch_mod, "persist_ranking", fake_persist)
    monkeypatch.setattr(Orchestrator, "_run_pages", fake_run_pages)
    return captured


def test_crawls_only_selected_top_n(wired):
    summary = asyncio.run(Orchestrator().run_site("x.com", target=TARGET))
    # top_n = 2 → only the two highest-scoring URLs are crawled, ranked order.
    assert wired["crawled"] == ["https://x.com/", "https://x.com/product/p"]
    assert summary.total == 2


def test_persists_full_ranking_not_just_selected(wired):
    asyncio.run(Orchestrator().run_site("x.com", target=TARGET))
    persisted = wired["persisted"]
    assert [p[0] for p in persisted] == [
        "https://x.com/",
        "https://x.com/product/p",
        "https://x.com/blog/a",
        "https://x.com/blog/b",
    ]
    assert [p[2] for p in persisted] == [True, True, False, False]  # all 4 rows, 2 selected


def test_default_label_names_the_site(wired):
    asyncio.run(Orchestrator().run_site("x.com", target=TARGET))
    assert wired["label"] == "site:x.com"


def test_explicit_label_is_kept(wired):
    asyncio.run(Orchestrator().run_site("x.com", target=TARGET, label="weekly"))
    assert wired["label"] == "weekly"


def test_empty_discovery_finishes_run_without_crawling(monkeypatch):
    async def empty_discover(domain, **_):
        return DiscoveryResult(domain="https://x.com/", source="seed", urls=[])

    finished: dict = {}

    monkeypatch.setattr(orch_mod, "discover", empty_discover)
    monkeypatch.setattr(orch_mod, "load_prioritization_cfg", lambda: CFG)
    monkeypatch.setattr(
        orch_mod.runs_repo, "start",
        lambda label=None, run_key=None: CrawlRun(
            id=7, run_key="r", label=label, started_at=datetime(2026, 6, 1), status="running"
        ),
    )
    monkeypatch.setattr(orch_mod, "persist_ranking", lambda run_id, scored: None)
    monkeypatch.setattr(orch_mod.runs_repo, "finish",
                        lambda run_id, status="succeeded", notes=None: finished.update(status=status))

    def boom(*a, **k):
        raise AssertionError("_run_pages must not be called when nothing is discovered")

    monkeypatch.setattr(Orchestrator, "_run_pages", boom)

    summary = asyncio.run(Orchestrator().run_site("x.com", target=TARGET))
    assert summary.total == 0
    assert finished["status"] == "succeeded"
