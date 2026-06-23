"""AgentRunController: research → plan → build → staged, and the failure path. Injected fakes."""

from __future__ import annotations

import pytest

from aeo.settings import AgentsCfg


class FakeRepo:
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


def _ctrl(repo, *, research=None, planner=None, builder=None, cfg=None):
    from aeo.agents.runtime import AgentRunController

    return AgentRunController(
        research=research or (lambda brief, **kw: {"competitors": []}),
        planner=planner or (lambda brief: {"topic": "ctem", "tasks": [{"id": "t", "kind": "content"}]}),
        builder=builder or (lambda graph, **kw: graph),
        repo=repo,
        llm_provider=lambda: None,
        cfg=cfg or AgentsCfg(),
    )


def test_full_flow_records_research_plan_build_in_order() -> None:
    repo = FakeRepo(_row())
    research = lambda brief, **kw: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]}
    planner = lambda brief: {"topic": "ctem", "tasks": [{"id": "page:/x", "kind": "content"}]}
    builder = lambda graph, **kw: {**graph, "built": True}

    out = _ctrl(repo, research=research, planner=planner, builder=builder).run("run1")

    assert out["status"] == "staged"
    assert out["result"]["built"] is True
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [(1, "research"), (2, "planner"), (3, "builder")]


def test_competitors_are_folded_into_the_brief() -> None:
    repo = FakeRepo(_row())
    seen = {}

    def planner(brief):
        seen["competitors"] = brief.competitors
        return {"topic": "ctem", "tasks": []}

    research = lambda brief, **kw: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]}
    _ctrl(repo, research=research, planner=planner).run("run1")
    assert seen["competitors"] == ["rapid7.com"]


def test_flags_off_runs_planner_only() -> None:
    repo = FakeRepo(_row())
    cfg = AgentsCfg(research_enabled=False, build_enabled=False)
    _ctrl(repo, cfg=cfg).run("run1")
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [(1, "planner")]


def test_planner_failure_marks_failed_and_reraises() -> None:
    repo = FakeRepo(_row())

    def boom(brief):
        raise RuntimeError("planner exploded")

    cfg = AgentsCfg(research_enabled=False, build_enabled=False)
    with pytest.raises(RuntimeError, match="planner exploded"):
        _ctrl(repo, planner=boom, cfg=cfg).run("run1")
    assert repo.runs["run1"]["status"] == "failed"
    assert repo.steps[0]["status"] == "failed"
    assert repo.steps[0]["error_class"] == "RuntimeError"


def test_terminal_status_is_a_noop() -> None:
    repo = FakeRepo(_row(status="approved"))
    called = []
    _ctrl(repo, planner=lambda brief: called.append(1) or {}).run("run1")
    assert repo.runs["run1"]["status"] == "approved"
    assert called == []
