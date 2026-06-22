"""AgentRunController: plan→staged transitions and the failure path, via injected fakes."""

from __future__ import annotations

import pytest


class FakeRepo:
    """In-memory stand-in for storage.repos.agent_runs (only the methods the controller uses)."""

    def __init__(self, run: dict) -> None:
        self.runs = {run["id"]: dict(run)}
        self.steps: list[dict] = []

    def get(self, run_id):
        r = self.runs.get(run_id)
        return dict(r) if r else None

    def set_status(self, run_id, status, *, current_step=None, result=None, error=None):
        r = self.runs[run_id]
        r["status"] = status
        if current_step is not None:
            r["current_step"] = current_step
        if result is not None:
            r["result"] = result
        if error is not None:
            r["error"] = error
        return True

    def append_step(self, run_id, **kw):
        self.steps.append({"run_id": run_id, **kw})
        return len(self.steps)


def _row(brief=None, status="queued"):
    return {"id": "run1", "status": status, "brief": brief or {"name": "Acme", "domain": "acme.com"}}


def test_run_plans_and_stages() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row())
    graph = {"tasks": [{"id": "page:home"}, {"id": "page:about"}]}
    ctrl = AgentRunController(planner=lambda brief: graph, repo=repo)

    out = ctrl.run("run1")
    assert out["status"] == "staged"
    assert out["result"] == graph
    assert repo.steps == [
        {"run_id": "run1", "seq": 1, "agent": "planner", "tool": "plan_from_brief",
         "status": "ok", "detail": {"task_count": 2}}
    ]


def test_run_records_failure_and_reraises() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row())

    def boom(brief):
        raise RuntimeError("planner exploded")

    ctrl = AgentRunController(planner=boom, repo=repo)
    with pytest.raises(RuntimeError, match="planner exploded"):
        ctrl.run("run1")

    assert repo.runs["run1"]["status"] == "failed"
    assert repo.steps[0]["status"] == "failed"
    assert repo.steps[0]["error_class"] == "RuntimeError"


def test_run_is_noop_on_terminal_status() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row(status="approved"))
    called = []
    ctrl = AgentRunController(planner=lambda brief: called.append(1) or {}, repo=repo)
    out = ctrl.run("run1")
    assert out["status"] == "approved"
    assert called == []  # already resolved → the planner never runs
