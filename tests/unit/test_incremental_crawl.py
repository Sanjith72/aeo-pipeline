"""
R2-2 — incremental crawl + drop-off safety + cache age.

Pins the new contract pieces, all offline (no DB, no network, no browser):
  * run_site emits the homepage-first ``profile`` partial BEFORE the slow crawl;
  * an explicit re-crawl (``force_recrawl``) bypasses the fingerprint skip gate;
  * a cancelled run stops before further per-page work and finishes ``partial``;
  * the cache-age helper surfaces when the homepage was last crawled.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aeo.api.jobs import JobRegistry
from aeo.crawl.discovery import DiscoveredUrl, DiscoveryResult
from aeo.crawl.prioritize import PrioritizationCfg
from aeo.pipeline import orchestrator as orch_mod
from aeo.pipeline.orchestrator import Orchestrator, RunSummary
from aeo.storage.models import CrawlRun, Target

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


def _wire_discovery(monkeypatch):
    async def fake_discover(domain, *, fetch=None, max_urls=None, prefer_recursive=False):
        return DiscoveryResult(
            domain="https://x.com/", source="sitemap",
            urls=[
                DiscoveredUrl("https://x.com/", 20),
                DiscoveredUrl("https://x.com/product/p", 10),
                DiscoveredUrl("https://x.com/blog/a", 8),
            ],
        )

    monkeypatch.setattr(orch_mod, "discover", fake_discover)
    monkeypatch.setattr(orch_mod, "load_prioritization_cfg", lambda: CFG)
    monkeypatch.setattr(
        orch_mod.runs_repo, "start",
        lambda label=None, run_key=None: CrawlRun(
            id=42, run_key="r", label=label, started_at=datetime(2026, 6, 1), status="running"
        ),
    )
    monkeypatch.setattr(orch_mod, "persist_ranking", lambda run_id, scored: None)
    # The blueprint/coverage block hits the DB — skip it cleanly so the test stays offline.
    monkeypatch.setattr(orch_mod, "generate_and_pin_blueprint", lambda *a, **k: None)


def test_homepage_profile_emitted_before_crawl(monkeypatch):
    _wire_discovery(monkeypatch)
    events: list[str] = []

    async def fake_run_pages(self, urls, *, run, target, do_score, progress=None,
                             force_recrawl=False, should_cancel=None):
        events.append("crawl")
        return RunSummary(run_id=run.id, run_key=run.run_key)

    monkeypatch.setattr(Orchestrator, "_run_pages", fake_run_pages)

    seen: list[tuple[str, dict]] = []

    def sink(stage, counts):
        events.append(stage)
        seen.append((stage, counts))

    asyncio.run(Orchestrator().run_site("x.com", target=TARGET, progress=sink))

    # The homepage partial is surfaced, and it arrives before the slow crawl.
    assert "profile" in events
    assert events.index("profile") < events.index("crawl")
    prof_counts = dict(next(c for s, c in seen if s == "profile"))
    assert "industry" in prof_counts and "headline" in prof_counts


def test_homepage_profile_skipped_without_a_sink(monkeypatch):
    # No progress sink → the partial profile is never built (CLI/test paths pay nothing).
    _wire_discovery(monkeypatch)
    built = {"n": 0}

    def spy_build(*a, **k):
        built["n"] += 1
        raise AssertionError("build_site_profile must not run without a sink")

    monkeypatch.setattr("aeo.intelligence.build_site_profile", spy_build)

    async def fake_run_pages(self, urls, *, run, target, do_score, progress=None,
                             force_recrawl=False, should_cancel=None):
        return RunSummary(run_id=run.id, run_key=run.run_key)

    monkeypatch.setattr(Orchestrator, "_run_pages", fake_run_pages)
    asyncio.run(Orchestrator().run_site("x.com", target=TARGET))  # progress=None
    assert built["n"] == 0


def _stub_stages(orch: Orchestrator) -> None:
    """Replace the persist/extract/score seams so _process_one runs with no DB."""
    orch.persist = SimpleNamespace(
        page=lambda *a, **k: SimpleNamespace(id=1),
        extraction=lambda *a, **k: 1,
        score=lambda *a, **k: 1,
        copy_unchanged=lambda *a, **k: True,
    )
    orch.extract = SimpleNamespace(run=lambda *a, **k: SimpleNamespace())
    orch.score = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(criteria={}))


def _page(url: str = "https://x.com/"):
    return SimpleNamespace(success=True, url=url, url_normalized=url, content_hash="h")


def test_force_recrawl_bypasses_should_skip(monkeypatch):
    orch = Orchestrator()
    _stub_stages(orch)
    consulted = {"skip": False}
    monkeypatch.setattr(
        orch_mod.fingerprint, "should_skip",
        lambda *a, **k: consulted.__setitem__("skip", True) or True,
    )
    summary = RunSummary(run_id=1, run_key="r")
    orch._process_one(_page(), 1, 1, None, {}, summary, do_score=True, force_recrawl=True)
    # The skip gate was never consulted, so the page was fully (re)processed.
    assert consulted["skip"] is False
    assert summary.scored == 1 and summary.unchanged == 0


def test_skip_gate_is_consulted_without_force(monkeypatch):
    orch = Orchestrator()
    _stub_stages(orch)
    consulted = {"skip": False}
    monkeypatch.setattr(
        orch_mod.fingerprint, "should_skip",
        lambda *a, **k: consulted.__setitem__("skip", True) or True,
    )
    summary = RunSummary(run_id=1, run_key="r")
    orch._process_one(_page(), 1, 1, None, {}, summary, do_score=True, force_recrawl=False)
    assert consulted["skip"] is True
    assert summary.unchanged == 1 and summary.scored == 0  # short-circuited as unchanged


def test_cancellation_stops_further_page_work(monkeypatch):
    orch = Orchestrator()
    processed = {"n": 0}

    async def fake_fetch_many(urls):
        return [_page(u) for u in urls]

    monkeypatch.setattr(orch_mod, "fetch_many", fake_fetch_many)
    finished: dict = {}
    monkeypatch.setattr(
        orch_mod.runs_repo, "finish",
        lambda run_id, status="succeeded", notes=None: finished.update(status=status, notes=notes),
    )

    def fake_process_one(self, page, *a, **k):
        processed["n"] += 1

    monkeypatch.setattr(Orchestrator, "_process_one", fake_process_one)

    # Cancel as soon as one page has been processed.
    def should_cancel() -> bool:
        return processed["n"] >= 1

    run = SimpleNamespace(id=1, run_key="r")
    summary = asyncio.run(
        orch._run_pages(
            ["https://x.com/a", "https://x.com/b", "https://x.com/c"],
            run=run, target=TARGET, do_score=False, should_cancel=should_cancel,
        )
    )
    assert processed["n"] == 1  # the other two pages were never touched
    assert finished["status"] == "partial"
    assert finished["notes"] == "cancelled mid-run"
    assert summary.total == 3


def test_registry_request_cancel_sets_flag():
    reg = JobRegistry()
    job = reg.create("audit")
    assert job.cancelled is False
    assert reg.request_cancel(job.id) is job
    assert reg.get(job.id).cancelled is True
    assert reg.request_cancel("missing") is None


def test_cache_age_helper(monkeypatch):
    from aeo.api.app import _cache_age

    three_h_ago = datetime.now(UTC) - timedelta(hours=3)
    monkeypatch.setattr("aeo.storage.repos.pages.last_crawled_at", lambda _u: three_h_ago)
    out = _cache_age("x.com")
    assert out["cache_age_hours"] is not None
    assert 2.5 <= out["cache_age_hours"] <= 3.5
    assert out["last_crawled_at"]


def test_cache_age_none_when_never_crawled(monkeypatch):
    from aeo.api.app import _cache_age

    monkeypatch.setattr("aeo.storage.repos.pages.last_crawled_at", lambda _u: None)
    out = _cache_age("x.com")
    assert out == {"last_crawled_at": None, "cache_age_hours": None}


def test_cache_age_survives_a_down_db(monkeypatch):
    from aeo.api.app import _cache_age

    def boom(_u):
        raise RuntimeError("db down")

    monkeypatch.setattr("aeo.storage.repos.pages.last_crawled_at", boom)
    assert _cache_age("x.com") == {"last_crawled_at": None, "cache_age_hours": None}
