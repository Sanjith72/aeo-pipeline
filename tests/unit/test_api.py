"""SP-4a HTTP API — endpoint wiring over the existing aeo functions (TestClient)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")  # skip when the optional [api] extra isn't installed

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_health_never_500s_without_db() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] in {"ok", "unreachable"}


def test_plan_no_website() -> None:
    r = client.post("/api/plan", json={"name": "Acme", "domain": "acme.com", "topic": "ctem"})
    assert r.status_code == 200
    body = r.json()
    assert body["profile"]["scenario"] == "no_website"
    assert body["profile"]["deliverable"] == "AEO Website Blueprint"
    assert body["blueprint"]["ideal_pages"] > 0
    assert body["business"]["key"] == "acme.com"


def test_blueprint_default_framework() -> None:
    r = client.post("/api/blueprint", json={"topic": "ctem"})
    assert r.status_code == 200
    body = r.json()
    assert body["topic"]
    assert len(body["sitemap"]) > 0


def test_deliverables_returns_inline_bundle() -> None:
    r = client.post("/api/deliverables", json={"name": "Acme", "domain": "acme.com", "draft_limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["manifest"]["asset_count"] > 0
    paths = {a["path"] for a in body["assets"]}
    assert "sitemap.xml" in paths
    assert any(p.startswith("pages/") for p in paths)
    # draft_limit honored
    assert sum(1 for a in body["assets"] if a["kind"] == "page_spec") == 3


def test_deliverables_returns_structured_phased_plan() -> None:
    # #10: the prioritized plan as structured JSON — phased, quick-wins flagged, each
    # page task with the three fields + AI-vs-human prompts.
    r = client.post(
        "/api/deliverables",
        json={"name": "Acme", "domain": "acme.com", "use_llm": False, "draft_limit": 2, "builder_mode": "ai"},
    )
    assert r.status_code == 200
    plan = r.json()["plan"]
    assert plan["total"] > 0
    assert any(p["key"] == "week_1" for p in plan["phases"])
    assert plan["quick_win_count"] >= 1  # at least the GBP visibility win
    page_tasks = [t for p in plan["phases"] for t in p["tasks"] if t["id"].startswith("page:")]
    assert page_tasks
    assert all(t["prompts"]["ai"] and t["prompts"]["human"] for t in page_tasks)
    assert all(t["current_state"] and t["action_required"] and t["how_to"] for t in page_tasks)
    # legacy checklist kept as the zip fallback
    assert r.json()["checklist"]["total"] > 0


def test_deliverables_returns_strategy_clusters() -> None:
    # R2-5: the same tasks clustered by difficulty/maturity grade (the Strategy tab).
    r = client.post(
        "/api/deliverables",
        json={"name": "Acme", "domain": "acme.com", "use_llm": False, "draft_limit": 2, "builder_mode": "ai"},
    )
    assert r.status_code == 200
    strategy = r.json()["strategy"]
    assert strategy["total"] > 0
    assert strategy["groups"], "expected at least one difficulty group"
    for g in strategy["groups"]:
        assert set(g["readme"]) == {"what", "why", "how"}
        assert g["task_ids"] == [t["id"] for t in g["tasks"]]
    # grades follow the maturity ladder (foundation before growth before advanced)
    grades = strategy["grades"]
    assert grades == sorted(grades, key=["foundation", "growth", "advanced"].index)


def test_deliverables_zip_returns_a_zip() -> None:
    r = client.post("/api/deliverables.zip", json={"name": "Acme", "domain": "acme.com", "draft_limit": 2})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.content[:2] == b"PK"  # zip magic bytes


def test_plan_requires_name() -> None:
    r = client.post("/api/plan", json={})  # 'name' is required
    assert r.status_code == 422


def test_audit_job_lifecycle_with_fake_runner(monkeypatch) -> None:
    # Inject a fake audit runner so no DB/crawl is needed. The audit runs on a worker
    # thread (so it can't block the API loop), so poll until it reaches a terminal state.
    import time

    from aeo.api import jobs as jobs_mod

    async def fake_runner(domain: str, name: str, progress=None, **_):
        if progress is not None:  # drive a couple of stage updates through the job
            progress("discover", {"discovered": 5})
            progress("report", {"site_report_id": 1})
        return {"run": {"run_id": 7}, "domain": domain, "name": name}

    monkeypatch.setattr(jobs_mod, "default_audit_runner", fake_runner)
    r = client.post("/api/audit", json={"domain": "acme.com", "name": "Acme"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id

    body: dict = {}
    for _ in range(100):
        body = client.get(f"/api/audit/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.02)
    assert body["status"] == "succeeded"
    assert body["result"]["run"]["run_id"] == 7
    # #7: the per-stage progress the runner emitted is polled back on the job
    assert [s["stage"] for s in body["stages"]] == ["discover", "report"]


def test_audit_status_404() -> None:
    assert client.get("/api/audit/does-not-exist").status_code == 404


def test_audit_cancel_unknown_job_404() -> None:
    assert client.post("/api/audit/does-not-exist/cancel").status_code == 404


def test_audit_cancel_flags_a_running_job() -> None:
    from aeo.api.jobs import JOBS

    job = JOBS.create("audit")
    r = client.post(f"/api/audit/{job.id}/cancel")
    assert r.status_code == 200
    assert r.json()["cancelled"] is True


def test_event_records_via_repo(monkeypatch) -> None:
    # Block F: POST /api/events delegates to events_repo.record (monkeypatched → no DB).
    from aeo.storage.repos import events as events_repo

    captured: dict = {}

    def fake_record(session_id, event_type, *, client_id=None, url=None, metadata=None):
        captured.update(session_id=session_id, event_type=event_type, metadata=metadata)
        return 123

    monkeypatch.setattr(events_repo, "record", fake_record)
    r = client.post(
        "/api/events",
        json={"session_id": "s1", "event_type": "plan_viewed", "metadata": {"quick_win_ids": ["a"]}},
    )
    assert r.status_code == 200
    assert r.json() == {"recorded": True, "id": 123}
    assert captured == {"session_id": "s1", "event_type": "plan_viewed", "metadata": {"quick_win_ids": ["a"]}}


def test_event_is_best_effort_when_db_down(monkeypatch) -> None:
    # Analytics must never break the user's flow — a record failure is swallowed (200).
    from aeo.storage.repos import events as events_repo

    def boom(*a, **k):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(events_repo, "record", boom)
    r = client.post("/api/events", json={"session_id": "s1", "event_type": "session_start"})
    assert r.status_code == 200
    assert r.json() == {"recorded": False}


def test_event_requires_session_and_type() -> None:
    # blank session_id / event_type → 422 (pydantic accepts the strings, the handler rejects)
    assert client.post("/api/events", json={"session_id": " ", "event_type": "x"}).status_code == 422
    assert client.post("/api/events", json={"session_id": "s", "event_type": " "}).status_code == 422


def test_metrics_returns_repo_payload(monkeypatch) -> None:
    from aeo.storage.repos import events as events_repo

    fake = {"dau": [], "return_rate": 0.0, "quick_win_completion_rate": 0.0,
            "recommendation_implementation_rate": 0.0}
    monkeypatch.setattr(events_repo, "metrics", lambda *a, **k: fake)
    r = client.get("/api/metrics")
    assert r.status_code == 200
    assert r.json() == fake


def test_auth_open_when_no_key_configured() -> None:
    # default test env has no AEO__API__AUTH_KEY → endpoints are open (no header)
    assert client.post("/api/plan", json={"name": "Acme", "domain": "acme.com"}).status_code == 200


def test_auth_enforced_when_key_configured(monkeypatch) -> None:
    from aeo.settings import get_settings

    # set the key on the live (cached) settings the guard reads; monkeypatch restores it
    monkeypatch.setattr(get_settings().api, "auth_key", "s3cret")
    # missing / wrong key → 401
    assert client.post("/api/plan", json={"name": "Acme", "domain": "acme.com"}).status_code == 401
    assert client.post("/api/plan", json={"name": "Acme"}, headers={"X-API-Key": "nope"}).status_code == 401
    # correct key → 200
    ok = client.post("/api/plan", json={"name": "Acme", "domain": "acme.com"}, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
    # health stays open even with a key configured
    assert client.get("/api/health").status_code == 200


def test_share_guard_exempts_only_readonly_get() -> None:
    """The auth guard must keep the public read-only GET /api/share/{token} open while
    still gating owner actions under /api/share/ (POST /api/share/rotate). Tests the pure
    guard so it needs no DB."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from fastapi import HTTPException

    from aeo.api.app import require_api_key

    def req(path: str, method: str, key: str | None = None):
        return SimpleNamespace(
            url=SimpleNamespace(path=path),
            method=method,
            headers=({"x-api-key": key} if key else {}),
        )

    with patch("aeo.api.app.current_api_key", lambda: "s3cret"):
        # public read-only view: exempt regardless of key (no raise)
        require_api_key(req("/api/share/sometoken", "GET"))
        # owner rotate: a POST under /api/share/ is NOT exempt → 401 without the key
        with pytest.raises(HTTPException) as ei:
            require_api_key(req("/api/share/rotate", "POST"))
        assert ei.value.status_code == 401
        # with the right key it passes
        require_api_key(req("/api/share/rotate", "POST", key="s3cret"))


def test_audit_requires_domain() -> None:
    assert client.post("/api/audit", json={"domain": "  "}).status_code == 422


def test_site_report_404_for_unknown_run() -> None:
    # No DB row (and a down DB) → a clean 404/5xx, never an unhandled crash in the client.
    try:
        r = client.get("/api/site-report/99999999")
    except Exception:
        pytest.skip("site-report path requires a reachable DB")
    assert r.status_code in {404, 500, 502, 503}


class _FakeLLM:
    enabled = True

    def generate_json(self, prompt: str, system: str | None = None) -> dict:
        return {
            "competitors": [
                {"name": "Rapid7", "domain": "rapid7.com", "aliases": ["R7"]},
                {"name": "Tenable", "domain": "https://www.tenable.com", "aliases": []},
                {"name": "Acme", "domain": "acme.com"},  # the company itself — must be dropped
            ]
        }


def test_competitor_suggest_returns_names(monkeypatch) -> None:
    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _FakeLLM())
    r = client.post(
        "/api/competitors/suggest",
        json={"name": "Acme", "domain": "acme.com", "category": "cybersecurity", "location": "Boston"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "llm"
    domains = [c["domain"] for c in body["competitors"]]
    # verify=False (default) skips live HEAD checks, so both candidates survive,
    # the messy domain is normalized, and the company itself is excluded.
    assert domains == ["rapid7.com", "tenable.com"]


def test_competitor_suggest_passes_services_into_the_llm_prompt(monkeypatch) -> None:
    # The crawled offerings sharpen "direct competitor" — they must reach the LLM prompt.
    seen: dict = {}

    class _CapturingLLM:
        enabled = True

        def generate_json(self, prompt: str, system: str | None = None) -> dict:
            seen["prompt"] = prompt
            return {"competitors": [{"name": "Rapid7", "domain": "rapid7.com"}]}

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _CapturingLLM())
    r = client.post(
        "/api/competitors/suggest",
        json={"name": "Acme", "domain": "acme.com", "services": ["Penetration Testing", "Attack Surface Management"]},
    )
    assert r.status_code == 200
    assert "Penetration Testing" in seen["prompt"]
    assert "Attack Surface Management" in seen["prompt"]


def test_competitor_suggest_unavailable_without_llm(monkeypatch) -> None:
    class _Disabled:
        enabled = False

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _Disabled())
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    # reason=llm_disabled: config, not luck — the UI says "AI is off on this server"
    # instead of blaming the business for having no findable peers.
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "llm_disabled"}


def test_competitor_suggest_no_results_when_llm_answers_empty(monkeypatch) -> None:
    # A WORKING LLM that genuinely proposes nothing → reason=no_results (an honest
    # blank), never llm_failed (which would tell the UI to nudge a retry).
    class _EmptyLLM:
        enabled = True

        def generate_json(self, prompt: str, system: str | None = None) -> dict:
            return {"competitors": []}

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _EmptyLLM())
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "no_results"}


def test_competitor_suggest_bad_llm_shape_is_llm_failed_not_500(monkeypatch) -> None:
    # A model answering with a NON-LIST competitors value (single object — classic
    # small-model JSON drift) must degrade to the honest llm_failed, never crash the
    # endpoint through discover_competitors' never-raises contract.
    class _BadShapeLLM:
        enabled = True

        def generate_json(self, prompt: str, system: str | None = None) -> dict:
            return {"competitors": {"name": "Rapid7", "domain": "rapid7.com"}}

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _BadShapeLLM())
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "llm_failed"}


def test_competitor_suggest_verification_failure_is_not_no_results(monkeypatch) -> None:
    # verify=true + the LLM proposing candidates + every live probe failing: the AI
    # worked and the domains were dropped by infrastructure — that's
    # "verification_failed", never the honest-blank no_results (the original bug's
    # copy) and never llm_failed (which would blame the providers).
    class _ProposingLLM:
        enabled = True

        def generate_json(self, prompt: str, system: str | None = None) -> dict:
            return {"competitors": [{"name": "Rapid7", "domain": "rapid7.com"}]}

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _ProposingLLM())
    monkeypatch.setattr(
        "aeo.reference.competitor_discovery._default_head_check", lambda _d: False
    )
    r = client.post("/api/competitors/suggest", json={"name": "Acme", "verify": True})
    assert r.status_code == 200
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "verification_failed"}


def test_competitor_suggest_budget_expiry_is_llm_failed(monkeypatch) -> None:
    # The whole-request budget (_SUGGEST_BUDGET_SEC) must cut a hung discovery walk and
    # answer with the retry-worthy llm_failed — not let the proxy in front 504 and
    # collapse the reason into a generic error.
    import time as _time

    class _Enabled:
        enabled = True

    def slow_discover(*_a, **_k):
        _time.sleep(0.5)
        from aeo.reference.competitor_discovery import CompetitorDiscoveryResult

        return CompetitorDiscoveryResult()

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _Enabled())
    monkeypatch.setattr("aeo.api.app._SUGGEST_BUDGET_SEC", 0.05)
    monkeypatch.setattr(
        "aeo.reference.competitor_discovery.discover_competitors", slow_discover
    )
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "llm_failed"}


def test_competitor_suggest_requires_name() -> None:
    assert client.post("/api/competitors/suggest", json={"name": "  "}).status_code == 422


def test_deliverables_builder_mode_shapes_the_kit() -> None:
    req = {"name": "Acme", "domain": "acme.com", "use_llm": False, "draft_limit": 2}
    dev_paths = {a["path"] for a in client.post("/api/deliverables", json=req).json()["assets"]}
    assert "README.md" in dev_paths  # default stays the developer bundle

    ai = client.post("/api/deliverables", json={**req, "builder_mode": "ai"})
    assert ai.status_code == 200
    ai_paths = {a["path"] for a in ai.json()["assets"]}
    assert "START-HERE.md" in ai_paths
    assert "get-found-now.md" in ai_paths
    assert any(p.startswith("prompts/") for p in ai_paths)
    assert any(p.startswith("for-your-developer/") for p in ai_paths)

    # the structured 30-day plan rides along for the interactive checklist UI
    checklist = ai.json()["checklist"]
    assert checklist["total"] > 0
    assert any(t["id"].startswith("page:") for w in checklist["weeks"] for t in w["tasks"])
    assert any(t["id"] == "vis:gbp" for w in checklist["weeks"] for t in w["tasks"])

    assert client.post("/api/deliverables", json={**req, "builder_mode": "nope"}).status_code == 422


def test_profile_routes_dead_crawl_to_no_website(monkeypatch) -> None:
    # #3: a crawl that finds nothing must NOT 502 — it routes to the no-website path so
    # the URL-first flow always continues.
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {"profile": None, "coverage": {}, "discovered": 0, "source": "none"}

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(monkeypatch)
    r = client.post("/api/profile", json={"domain": "dead.example"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "dead"
    assert body["next"] == "/api/plan"
    assert body["industry"] is None and body["location"] is None


def test_profile_returns_inferred_industry_and_location(monkeypatch) -> None:
    # #2: a content-rich crawl returns the derived industry/location so the wizard
    # prefills them instead of asking.
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Software / SaaS", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0},
            "discovered": 12,
            "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(monkeypatch)
    r = client.post("/api/profile", json={"domain": "acme.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "rich"
    assert body["industry"] == "Software / SaaS"
    assert body["location"] is None


def _stub_site_facts(monkeypatch, *, location=None, services=None, competitors=None, industry=None,
                     wikidata=None, cms_type="unknown") -> None:
    """Stop /api/profile's homepage crawl AND Wikidata lookup from hitting the network.
    ``wikidata`` may be a bare industry string (shorthand for a Wikidata vertical) or a
    full :class:`WikidataProfile` (to exercise HQ/products/description fall-through)."""
    from aeo.intelligence.industry import WikidataProfile
    from aeo.intelligence.site_facts import SiteFacts

    async def fake_gather(domain, **_):
        return SiteFacts(
            location=location, services=services or [], competitors=competitors or [],
            industry=industry, cms_type=cms_type,
        )

    wiki = WikidataProfile(industry=wikidata) if isinstance(wikidata, str) else (wikidata or WikidataProfile())

    async def fake_wikidata(domain, **_):
        return wiki

    monkeypatch.setattr("aeo.intelligence.site_facts.gather_site_facts", fake_gather)
    monkeypatch.setattr("aeo.intelligence.industry.resolve_wikidata_profile", fake_wikidata)


def test_profile_includes_crawled_services_and_competitors(monkeypatch) -> None:
    # Location / what-you-offer / on-site competitors come back from the crawl so the
    # wizard's "About you" step arrives prefilled.
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Dentistry", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 40.0},
            "discovered": 9,
            "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(
        monkeypatch,
        location="Austin, TX",
        services=["Teeth Whitening", "Dental Implants"],
        competitors=[{"name": "Globex", "domain": ""}],
        cms_type="wordpress",
    )
    body = client.post("/api/profile", json={"domain": "harbor.com"}).json()
    assert body["location"] == "Austin, TX"  # content-derived location wins
    assert body["services"] == ["Teeth Whitening", "Dental Implants"]
    assert body["competitors"] == [{"name": "Globex", "domain": ""}]
    assert body["cms_type"] == "wordpress"  # threaded down to the dashboard's DIY steps


def test_profile_industry_prefers_wikidata_vertical(monkeypatch) -> None:
    # The Wikidata MERGE logic (gated off in prod — see _WIKIDATA_ENRICHMENT_ENABLED — because
    # WDQS can't answer inside the budget) still works when re-enabled: its specific vertical
    # beats the structural "Enterprise" coarse label. Flip the flag on to exercise the path.
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Enterprise", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0},
            "discovered": 20,
            "source": "sitemap",
        }

    import importlib
    monkeypatch.setattr(importlib.import_module("aeo.api.app"), "_WIKIDATA_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(monkeypatch, wikidata="Healthcare")
    body = client.post("/api/profile", json={"domain": "clevelandclinic.org"}).json()
    assert body["industry"] == "Healthcare"
    assert body["industry_source"] == "wikidata"


def test_profile_wikidata_enrichment_on_by_default(monkeypatch) -> None:
    # Enrichment ships ON now that the resolver uses the fast entity-API path (the old
    # WDQS scan was too slow, which is why this used to be gated off). Wikidata is the
    # best-precision industry source, so it must win over the crawl-classified vertical.
    from aeo.intelligence.industry import WikidataProfile
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Enterprise", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0}, "discovered": 18, "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(
        monkeypatch,
        industry="Finance",  # crawl-classified vertical — loses to Wikidata
        wikidata=WikidataProfile(industry="Healthcare", location="London", country="United Kingdom",
                                 offerings=["X"], description="a description"),
    )
    body = client.post("/api/profile", json={"domain": "acme.com"}).json()
    assert body["industry"] == "Healthcare"
    assert body["industry_source"] == "wikidata"
    assert body["location"] == "London, United Kingdom"
    assert body["about"] == "a description"


def test_profile_enriches_location_services_about_from_wikidata(monkeypatch) -> None:
    # A non-curated site whose crawl found no address/offerings still prefills from
    # Wikidata's HQ (P159 + P17 country), products produced (P1056), and description.
    from aeo.intelligence.industry import WikidataProfile
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Enterprise", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0}, "discovered": 18, "source": "sitemap",
        }

    import importlib
    monkeypatch.setattr(importlib.import_module("aeo.api.app"), "_WIKIDATA_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    # Crawl finds nothing usable for location/services; Wikidata supplies them.
    _stub_site_facts(
        monkeypatch,
        wikidata=WikidataProfile(
            industry="Software / SaaS",
            location="London",
            country="United Kingdom",
            offerings=["Endpoint Protection", "Threat Intelligence"],
            description="British cybersecurity software company",
        ),
    )
    body = client.post("/api/profile", json={"domain": "sophos.com"}).json()
    assert body["industry"] == "Software / SaaS"
    assert body["location"] == "London, United Kingdom"  # HQ + country, since crawl had none
    assert body["services"] == ["Endpoint Protection", "Threat Intelligence"]
    assert body["about"] == "British cybersecurity software company"


def test_profile_blocked_crawl_still_prefills_offer_from_wikidata_description(monkeypatch) -> None:
    # mayoclinic-shaped case: the site 403s every fetch (all crawl facts empty), but its
    # Wikidata entity carries the vertical + a one-line description. "What do you offer"
    # must surface the description's first clause rather than coming back blank.
    from aeo.intelligence.industry import WikidataProfile
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": None, "location": None, "scenario": "small_site"},
            "coverage": {"pct": 0.0}, "discovered": 12, "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(
        monkeypatch,  # crawl blocked → no location/services/industry
        wikidata=WikidataProfile(
            industry="Healthcare",
            location="Rochester",
            description="medical practice and medical research group based in Rochester, Minnesota",
        ),
    )
    body = client.post("/api/profile", json={"domain": "mayoclinic.org"}).json()
    assert body["industry"] == "Healthcare"
    assert body["industry_source"] == "wikidata"
    assert body["services"] == ["medical practice and medical research group"]
    assert body["location"] == "Rochester"


def test_profile_crawl_location_services_win_over_wikidata(monkeypatch) -> None:
    # When the crawl DID find an address/offerings, those (more precise) win; Wikidata is
    # only the fallback.
    from aeo.intelligence.industry import WikidataProfile
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Enterprise", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0}, "discovered": 18, "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    _stub_site_facts(
        monkeypatch,
        location="Austin, TX",
        services=["On-site Crawl Service"],
        wikidata=WikidataProfile(location="London", country="United Kingdom", offerings=["Wikidata Offering"]),
    )
    body = client.post("/api/profile", json={"domain": "acme.com"}).json()
    assert body["location"] == "Austin, TX"
    assert body["services"] == ["On-site Crawl Service"]


def test_profile_industry_falls_back_to_crawl_then_model(monkeypatch) -> None:
    from aeo.pipeline import Orchestrator

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True, draft_samples=True, **_):
        return {
            "profile": {"industry": "Enterprise", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0}, "discovered": 20, "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    # No Wikidata match, but the crawl classified a vertical → crawl wins over "Enterprise".
    _stub_site_facts(monkeypatch, industry="Finance", wikidata=None)
    body = client.post("/api/profile", json={"domain": "acme.com"}).json()
    assert body["industry"] == "Finance"
    assert body["industry_source"] == "crawl"


def test_competitor_suggest_falls_back_to_onsite_without_llm(monkeypatch) -> None:
    class _Disabled:
        enabled = False

    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _Disabled())

    from aeo.intelligence.site_facts import SiteFacts

    async def fake_gather(domain, **_):
        return SiteFacts(competitors=[{"name": "Globex", "domain": ""}])

    monkeypatch.setattr("aeo.intelligence.site_facts.gather_site_facts", fake_gather)
    r = client.post("/api/competitors/suggest", json={"name": "Acme", "domain": "acme.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "onsite"
    assert body["competitors"] == [{"name": "Globex", "domain": ""}]


class _BrokenLLM:
    """Configured but failing — daemon down or model not pulled: every call yields None."""

    enabled = True

    def generate_json(self, prompt: str, system: str | None = None) -> None:
        return None


def test_competitor_suggest_falls_back_to_onsite_when_llm_fails(monkeypatch) -> None:
    # An enabled-but-broken LLM must degrade exactly like the LLM-off path (mine the
    # site's own comparison pages) — not hand the UI an empty list labelled "llm".
    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _BrokenLLM())

    from aeo.intelligence.site_facts import SiteFacts

    async def fake_gather(domain, **_):
        return SiteFacts(competitors=[{"name": "Globex", "domain": ""}])

    monkeypatch.setattr("aeo.intelligence.site_facts.gather_site_facts", fake_gather)
    r = client.post("/api/competitors/suggest", json={"name": "Acme", "domain": "acme.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "onsite"
    assert body["competitors"] == [{"name": "Globex", "domain": ""}]


def test_competitor_suggest_unavailable_when_llm_fails_without_domain(monkeypatch) -> None:
    monkeypatch.setattr("aeo.nlp.llm.get_interactive_client", lambda: _BrokenLLM())
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    # reason=llm_failed: the model never answered usably — transient, so the UI shows
    # the retry-worthy error state instead of "no recommendations for this business".
    assert r.json() == {"competitors": [], "source": "unavailable", "reason": "llm_failed"}


def test_cors_allows_the_web_ui_origin() -> None:
    # the SP-4b UI on :3000 is a different origin — without this header every browser
    # fetch fails silently, so it's load-bearing for the whole guided flow
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ── retention foundation (Specs #1–#2) endpoint wiring ──────────────────────────


def test_resume_plan_blank_session_is_not_an_error() -> None:
    # the homepage 'resume' banner must read 'nothing to resume' as {id:null} (200),
    # never an error — a blank session never touches the DB.
    r = client.get("/api/plan-state")
    assert r.status_code == 200
    assert r.json() == {"id": None}


def test_site_freshness_is_best_effort_without_db() -> None:
    # the wizard freshness check (Slice 2b) must never break the flow — any miss/DB error
    # resolves to {fresh: false}.
    r = client.get("/api/site-freshness", params={"domain": "no-such-domain.example"})
    assert r.status_code == 200
    assert r.json()["fresh"] is False


def test_recheck_status_is_best_effort_without_db() -> None:
    # 'Verified live' + predicted fixes (Spec #2 · Feature #2) must never break the results
    # view — empty sets on any failure (verified, pending, count all present).
    r = client.get("/api/recheck-status", params={"domain": "no-such-domain.example"})
    assert r.status_code == 200
    assert r.json() == {"verified": [], "pending": [], "count": 0}


def test_create_plan_state_rejects_an_oversized_payload() -> None:
    # the public plan-state endpoint caps the stored blob so it can't be abused (B1).
    big = {"x": "y" * 1_100_000}
    r = client.post("/api/plan-state", json={"plan": big})
    assert r.status_code == 422  # bounded by _bounded_json before any DB write


def _plan_state_row(**overrides) -> dict:
    row = {
        "id": "p1", "run_id": None, "domain": "example.com", "plan": {"total": 0},
        "profile": None, "business_name": None, "score_snapshot": None,
        "done_task_ids": [], "created_at": None, "updated_at": None,
        "session_id": "keep-off-the-wire",
    }
    row.update(overrides)
    return row


def test_plan_state_read_backfills_run_id_from_domain(monkeypatch) -> None:
    # Every plan saved while the studio omitted run_id from the create call has NULL here,
    # and the resumed view's Pages tab (packs, page scores, workable fixes) is gated on it.
    # The read must resolve the domain's latest run, not serve a permanently degraded plan.
    monkeypatch.setattr("aeo.storage.repos.plan_state.get", lambda pid: _plan_state_row())
    monkeypatch.setattr(
        "aeo.storage.repos.runs.latest_for_domain",
        lambda domain: {"run_id": 42, "last_crawled_at": None, "status": "completed"},
    )
    r = client.get("/api/plan-state/p1")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == 42
    assert "session_id" not in body  # the allowlist still holds through the fallback path


def test_plan_state_read_keeps_a_saved_run_id(monkeypatch) -> None:
    # A saved run id is authoritative — the fallback must not swap it for a newer run,
    # which could silently re-key the plan's packs to an audit the user never saw.
    monkeypatch.setattr("aeo.storage.repos.plan_state.get", lambda pid: _plan_state_row(run_id=7))
    called: list[str] = []
    monkeypatch.setattr(
        "aeo.storage.repos.runs.latest_for_domain",
        lambda domain: called.append(domain) or {"run_id": 99},
    )
    r = client.get("/api/plan-state/p1")
    assert r.status_code == 200
    assert r.json()["run_id"] == 7
    assert called == []  # never even consulted


def test_plan_state_read_survives_a_broken_run_fallback(monkeypatch) -> None:
    # The fallback is a nicety; a DB error inside it must not take down the plan itself.
    monkeypatch.setattr("aeo.storage.repos.plan_state.get", lambda pid: _plan_state_row())
    def boom(domain: str):
        raise RuntimeError("no db")
    monkeypatch.setattr("aeo.storage.repos.runs.latest_for_domain", boom)
    r = client.get("/api/plan-state/p1")
    assert r.status_code == 200
    assert r.json()["run_id"] is None


# ── audit endpoint hardening (SSRF guard + dedupe) ──────────────────────────────


def test_audit_rejects_an_internal_host() -> None:
    # SSRF guard: a domain resolving to loopback/internal must be rejected (400), so a crawl
    # can't be pointed at internal infra (e.g. cloud metadata).
    assert client.post("/api/audit", json={"domain": "localhost"}).status_code == 400


def test_audit_dedupes_an_in_flight_domain(monkeypatch) -> None:
    # double-clicks / overlapping requests for the same domain collapse onto one job.
    import asyncio

    from aeo.api import jobs as jobs_mod

    async def slow_runner(domain, name, progress=None, should_cancel=None, **kw):
        for _ in range(500):
            if should_cancel and should_cancel():
                return {"run": {"run_id": 1}, "cancelled": True}
            await asyncio.sleep(0.01)
        return {"run": {"run_id": 1}}

    monkeypatch.setattr(jobs_mod, "default_audit_runner", slow_runner)
    a = client.post("/api/audit", json={"domain": "acme.com"}).json()["job_id"]
    b = client.post("/api/audit", json={"domain": "acme.com"}).json()["job_id"]
    assert a == b  # the second request found the in-flight job
    jobs_mod.JOBS.request_cancel(a)  # let the slow runner wind down


# ── per-IP rate limiting ────────────────────────────────────────────────────────


def test_rate_limit_throttles_over_the_cap_but_exempts_health(monkeypatch) -> None:
    import sys

    app_mod = sys.modules["aeo.api.app"]  # the module, not the re-exported FastAPI instance
    from aeo.settings import get_settings

    # tiny cap on the live (cached) settings; fresh limiter so the test is isolated
    monkeypatch.setattr(get_settings().api, "rate_limit", 3)
    monkeypatch.setattr(get_settings().api, "rate_window_sec", 60)
    monkeypatch.setattr(app_mod, "_RATE", app_mod._RateLimiter())

    # /api/health is never limited (liveness probes must always pass)
    assert all(client.get("/api/health").status_code == 200 for _ in range(8))

    # a limited route: first 3 pass, the rest get 429 + Retry-After
    codes = [client.get("/api/plan-state").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]
    blocked = client.get("/api/plan-state")
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers


def test_rate_limit_disabled_by_default(monkeypatch) -> None:
    import sys

    app_mod = sys.modules["aeo.api.app"]  # the module, not the re-exported FastAPI instance
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "rate_limit", 0)  # the default
    monkeypatch.setattr(app_mod, "_RATE", app_mod._RateLimiter())
    assert all(client.get("/api/plan-state").status_code == 200 for _ in range(10))
