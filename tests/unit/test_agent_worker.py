"""Worker dispatches the AGENT_RUN job kind to the AgentRunController."""

from __future__ import annotations


def test_enqueue_agent_run_uses_the_db_queue(monkeypatch) -> None:
    from aeo.pipeline import worker as worker_mod
    from aeo.storage.repos import jobs as jobs_repo

    seen = {}

    def fake_enqueue(kind, payload, run_after=None, max_attempts=4):
        seen.update(kind=kind, payload=payload, max_attempts=max_attempts)
        return 99

    monkeypatch.setattr(jobs_repo, "enqueue", fake_enqueue)
    job_id = worker_mod.enqueue_agent_run("run-abc")
    assert job_id == 99
    assert seen["kind"] == worker_mod.AGENT_RUN
    assert seen["payload"] == {"run_id": "run-abc"}


def test_dispatch_routes_agent_run_to_controller(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod
    from aeo.pipeline.worker import AGENT_RUN, Worker

    ran = {}

    class FakeController:
        def run(self, run_id):
            ran["run_id"] = run_id
            return {"id": run_id, "status": "staged"}

    monkeypatch.setattr(runtime_mod, "AgentRunController", FakeController)
    Worker()._dispatch({"kind": AGENT_RUN, "payload": {"run_id": "run-xyz"}})
    assert ran["run_id"] == "run-xyz"


def test_default_kinds_include_agent_run() -> None:
    from aeo.pipeline.worker import AGENT_RUN, Worker

    assert AGENT_RUN in Worker().kinds


def test_reap_fails_the_run_behind_a_dead_agent_job(monkeypatch) -> None:
    from aeo.pipeline import worker as worker_mod
    from aeo.storage.repos import agent_runs as agent_runs_repo
    from aeo.storage.repos import jobs as jobs_repo

    failed = {}
    only_from = {}
    monkeypatch.setattr(jobs_repo, "reap_stale", lambda lease: [
        {"id": 1, "kind": worker_mod.AGENT_RUN, "payload": {"run_id": "r-dead"}, "status": "dead"},
        # requeued job (attempts remain) — the run resumes, so it must NOT be failed
        {"id": 2, "kind": worker_mod.AGENT_RUN, "payload": {"run_id": "r-retry"}, "status": "pending"},
        # dead job of another kind — no agent run to fail
        {"id": 3, "kind": worker_mod.CRAWL_BATCH, "payload": {}, "status": "dead"},
    ])
    monkeypatch.setattr(
        agent_runs_repo, "set_status",
        lambda rid, status, **kw: ((failed.update({rid: status}), only_from.update({rid: kw.get("only_from")}))
        and True)
        or True,
    )
    worker_mod.Worker()._reap_stale()
    assert failed == {"r-dead": "failed"}
    # a run that already staged before its worker died must stay staged
    assert only_from["r-dead"] == ("queued", "planning")


def test_reap_is_throttled(monkeypatch) -> None:
    from aeo.pipeline import worker as worker_mod
    from aeo.storage.repos import jobs as jobs_repo

    calls = {"n": 0}
    monkeypatch.setattr(jobs_repo, "reap_stale", lambda lease: calls.update(n=calls["n"] + 1) or [])
    w = worker_mod.Worker()
    w._reap_stale()
    w._reap_stale()  # immediately again — inside the throttle window
    assert calls["n"] == 1
