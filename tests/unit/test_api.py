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
    monkeypatch.setattr("aeo.nlp.llm.get_client", lambda: _FakeLLM())
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


def test_competitor_suggest_unavailable_without_llm(monkeypatch) -> None:
    class _Disabled:
        enabled = False

    monkeypatch.setattr("aeo.nlp.llm.get_client", lambda: _Disabled())
    r = client.post("/api/competitors/suggest", json={"name": "Acme"})
    assert r.status_code == 200
    assert r.json() == {"competitors": [], "source": "unavailable"}


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

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True):
        return {"profile": None, "coverage": {}, "discovered": 0, "source": "none"}

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
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

    async def fake_dry_run(self, domain, *, max_urls=None, pages=0, use_llm=True):
        return {
            "profile": {"industry": "Software / SaaS", "location": None, "scenario": "small_site"},
            "coverage": {"pct": 50.0},
            "discovered": 12,
            "source": "sitemap",
        }

    monkeypatch.setattr(Orchestrator, "dry_run", fake_dry_run)
    r = client.post("/api/profile", json={"domain": "acme.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "rich"
    assert body["industry"] == "Software / SaaS"
    assert body["location"] is None


def test_cors_allows_the_web_ui_origin() -> None:
    # the SP-4b UI on :3000 is a different origin — without this header every browser
    # fetch fails silently, so it's load-bearing for the whole guided flow
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
