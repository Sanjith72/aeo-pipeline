"""Agent-run endpoints — wiring over the repo/service (monkeypatched, no DB)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_start_returns_run_id(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "start_agent_run",
                        lambda brief, **kw: {"id": "run42", "status": "queued"})
    r = client.post("/api/agent/run", json={"name": "Acme", "topic": "ctem"})
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "queued"}


def test_status_returns_run_and_steps(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged",
                                                  "result": {"tasks": []}, "domain": "acme.com"})
    monkeypatch.setattr(repo, "steps_for", lambda rid: [{"seq": 1, "agent": "planner"}])
    body = client.get("/api/agent/run/run42").json()
    assert body["status"] == "staged"
    assert body["steps"][0]["agent"] == "planner"


def test_status_404_for_unknown_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: None)
    assert client.get("/api/agent/run/nope").status_code == 404


def test_approve_flips_a_staged_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    captured = {}
    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: captured.update(rid=rid, status=status))
    r = client.post("/api/agent/run/run42/approve")
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "approved"}
    assert captured == {"rid": "run42", "status": "approved"}


def test_reject_flips_a_staged_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: None)
    r = client.post("/api/agent/run/run42/reject")
    assert r.json() == {"run_id": "run42", "status": "rejected"}


def test_approve_409_when_not_staged(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "approved"})
    assert client.post("/api/agent/run/run42/approve").status_code == 409
