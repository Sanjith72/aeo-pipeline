"""Agent-run endpoints — wiring over the repo/service (monkeypatched, no DB)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_start_returns_run_id(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "count_active", lambda: 0)
    monkeypatch.setattr(runtime_mod, "start_agent_run",
                        lambda brief, **kw: {"id": "run42", "status": "queued"})
    r = client.post("/api/agent/run", json={"name": "Acme", "topic": "ctem"})
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "queued"}


def test_start_passes_the_idempotency_key(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod
    from aeo.storage.repos import agent_runs as repo

    captured = {}
    monkeypatch.setattr(repo, "count_active", lambda: 0)
    monkeypatch.setattr(repo, "by_idempotency_key", lambda key: None)
    monkeypatch.setattr(runtime_mod, "start_agent_run",
                        lambda brief, **kw: captured.update(kw) or {"id": "run42", "status": "queued"})
    r = client.post("/api/agent/run", json={"name": "Acme", "idempotency_key": "click-abc"})
    assert r.status_code == 200
    assert captured["idempotency_key"] == "click-abc"


def test_start_429_when_too_many_runs_in_flight(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "count_active", lambda: 999)
    assert client.post("/api/agent/run", json={"name": "Acme"}).status_code == 429


def test_start_replays_an_existing_key_even_at_capacity(monkeypatch) -> None:
    # The retry whose first response was lost must learn its run id — its own run may be
    # exactly what fills the queue, so the replay answers before the 429 cap.
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "count_active", lambda: 999)
    monkeypatch.setattr(repo, "by_idempotency_key",
                        lambda key: {"id": "run-original", "status": "planning"})
    r = client.post("/api/agent/run", json={"name": "Acme", "idempotency_key": "click-abc"})
    assert r.status_code == 200
    assert r.json() == {"run_id": "run-original", "status": "planning"}


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
    monkeypatch.setattr(repo, "set_status",
                        lambda rid, status, **kw: captured.update(rid=rid, status=status, **kw) or True)
    r = client.post("/api/agent/run/run42/approve")
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "approved"}
    assert captured["rid"] == "run42"
    assert captured["status"] == "approved"
    assert captured["only_from"] == ("staged",)  # decisions are a CAS from 'staged'


def test_reject_flips_a_staged_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: True)
    r = client.post("/api/agent/run/run42/reject")
    assert r.json() == {"run_id": "run42", "status": "rejected"}


def test_approve_409_when_not_staged(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "approved"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: False)
    assert client.post("/api/agent/run/run42/approve").status_code == 409


def test_approve_409_when_it_loses_the_race(monkeypatch) -> None:
    # get() saw 'staged', but another decision/cancel settled the run before our write:
    # the CAS returns False and the caller must get a 409 with the REAL status — never
    # a 200 for a write that did not happen.
    from aeo.storage.repos import agent_runs as repo

    reads = iter([{"id": "run42", "status": "staged"}, {"id": "run42", "status": "cancelled"}])
    monkeypatch.setattr(repo, "get", lambda rid: next(reads))
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: False)
    r = client.post("/api/agent/run/run42/approve")
    assert r.status_code == 409
    assert "cancelled" in r.json()["detail"]


def test_cancel_a_queued_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo
    from aeo.storage.repos import jobs as jobs_repo

    captured = {}
    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "queued"})
    monkeypatch.setattr(repo, "set_status",
                        lambda rid, status, **kw: captured.update(rid=rid, status=status, **kw) or True)
    monkeypatch.setattr(jobs_repo, "cancel_pending",
                        lambda kind, rid: captured.update(job_kind=kind, job_run=rid) or 1)
    r = client.post("/api/agent/run/run42/cancel")
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "cancelled"}
    assert captured["status"] == "cancelled"
    assert captured["only_from"] == ("queued", "planning")  # CAS: staged runs never yanked
    assert captured["job_kind"] == "agent_run"


def test_cancel_409_for_a_terminal_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: False)
    r = client.post("/api/agent/run/run42/cancel")
    assert r.status_code == 409
    assert "staged" in r.json()["detail"]


def test_cancel_404_for_unknown_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: None)
    assert client.post("/api/agent/run/nope/cancel").status_code == 404


def test_list_agent_runs_returns_repo_rows(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "list_by_status", lambda status, limit=50: [{"id": "r1", "status": "staged"}])
    body = client.get("/api/agent/runs?status=staged").json()
    assert body == {"runs": [{"id": "r1", "status": "staged"}]}


def test_list_accepts_comma_separated_statuses(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    seen = {}
    monkeypatch.setattr(repo, "list_by_status",
                        lambda status, limit=50: seen.update(statuses=status) or [])
    assert client.get("/api/agent/runs?status=queued,planning").status_code == 200
    assert seen["statuses"] == ["queued", "planning"]


def test_list_400_on_unknown_status() -> None:
    assert client.get("/api/agent/runs?status=bogus").status_code == 400
    assert client.get("/api/agent/runs?status=").status_code == 400


def test_stream_emits_steps_then_done_for_a_terminal_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged",
                                                  "current_step": "review", "result": {"tasks": []}})
    monkeypatch.setattr(repo, "steps_for", lambda rid: [{"seq": 1, "agent": "planner", "status": "ok"}])

    with client.stream("GET", "/api/agent/run/r1/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert '"type": "step"' in body
    assert '"type": "done"' in body
    assert '"status": "staged"' in body


def test_stream_404s_for_unknown_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: None)
    with client.stream("GET", "/api/agent/run/nope/stream") as r:
        body = "".join(r.iter_text())
    assert '"type": "error"' in body
