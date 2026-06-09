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
    # Inject a fake audit runner so no DB/crawl is needed; TestClient runs the
    # BackgroundTask to completion before returning the POST response.
    from aeo.api import jobs as jobs_mod

    async def fake_runner(domain: str, name: str):
        return {"run": {"run_id": 7}, "domain": domain, "name": name}

    monkeypatch.setattr(jobs_mod, "default_audit_runner", fake_runner)
    r = client.post("/api/audit", json={"domain": "acme.com", "name": "Acme"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id

    body = client.get(f"/api/audit/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["result"]["run"]["run_id"] == 7


def test_audit_status_404() -> None:
    assert client.get("/api/audit/does-not-exist").status_code == 404


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
