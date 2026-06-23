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
