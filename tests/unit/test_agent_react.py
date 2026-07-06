"""
The agentic loop (agents/react.py) + tool registry (agents/tools.py) + the runtime's
react mode with its ladder fallback. All offline: the LLM is a scripted fake that
returns pre-baked decisions, the tools are the same injectable seams the ladder uses.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from aeo.agents.react import ReactAgent
from aeo.agents.runtime import AgentRunController
from aeo.agents.tools import Tool, ToolContext, ToolRegistry, planning_registry
from aeo.settings import AgentsCfg


class FakeLLM:
    """LLMClient-shaped: pops one scripted decision (a dict) per generate_json call."""

    def __init__(self, decisions: list[dict[str, Any] | None]) -> None:
        self.decisions = list(decisions)
        self.prompts: list[str] = []
        self.enabled = True
        self.model = "fake-model"

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False) -> str | None:
        return "text"

    def generate_json(self, prompt: str, system: str | None = None) -> dict[str, Any] | None:
        self.prompts.append(prompt)
        return self.decisions.pop(0) if self.decisions else None

    def embed(self, text: str) -> list[float] | None:
        return None


def _action(name: str, args: dict | None = None, thought: str = "step") -> dict[str, Any]:
    return {"thought": thought, "action": {"name": name, "args": args or {}}}


def _echo_registry() -> ToolRegistry:
    def echo(ctx: ToolContext, text: str) -> dict[str, Any]:
        return {"echo": text}

    def explode(ctx: ToolContext) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    def slow(ctx: ToolContext) -> dict[str, Any]:
        time.sleep(0.25)
        return {"ok": True}

    def done(ctx: ToolContext, summary: str) -> dict[str, Any]:
        ctx.done = True
        ctx.outcome = {"summary": summary}
        return {"staged": True}

    return ToolRegistry([
        Tool("echo", "echo text back",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
             echo),
        Tool("explode", "always raises", {"type": "object", "properties": {}, "required": []}, explode),
        Tool("slow", "sleeps 250ms", {"type": "object", "properties": {}, "required": []}, slow),
        Tool("done", "terminal",
             {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
             done),
    ])


class TestToolRegistry:
    def test_specs_advertise_schema(self):
        specs = _echo_registry().specs()
        echo = next(s for s in specs if s["name"] == "echo")
        assert echo["parameters"]["required"] == ["text"]

    def test_unknown_tool_returns_error_with_the_menu(self):
        obs = _echo_registry().invoke(ToolContext(), "nope", {})
        assert "unknown tool" in obs["error"]
        assert "echo" in obs["available_tools"]

    def test_missing_required_arg_is_an_error_observation(self):
        obs = _echo_registry().invoke(ToolContext(), "echo", {})
        assert "missing required argument" in obs["error"]

    def test_wrong_type_is_an_error_observation(self):
        obs = _echo_registry().invoke(ToolContext(), "echo", {"text": 42})
        assert "must be string" in obs["error"]

    def test_unexpected_arg_is_an_error_observation(self):
        obs = _echo_registry().invoke(ToolContext(), "echo", {"text": "hi", "bogus": 1})
        assert "unknown argument" in obs["error"]

    def test_handler_exception_never_raises(self):
        obs = _echo_registry().invoke(ToolContext(), "explode", {})
        assert obs["error"].startswith("RuntimeError")


class TestReactLoop:
    def test_happy_path_runs_tools_then_terminal(self):
        llm = FakeLLM([
            _action("echo", {"text": "hi"}),
            _action("done", {"summary": "all set"}),
        ])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("do it", ToolContext())
        assert result.status == "done"
        assert result.outcome == {"summary": "all set"}
        assert [s.tool for s in result.steps] == ["echo", "done"]

    def test_observations_are_fed_back_to_the_model(self):
        llm = FakeLLM([
            _action("echo", {"text": "marker-xyz"}),
            _action("done", {"summary": "ok"}),
        ])
        ReactAgent(llm, _echo_registry(), max_steps=5).run("do it", ToolContext())
        assert "marker-xyz" in llm.prompts[1]  # second turn sees the first observation

    def test_unknown_tool_lets_the_model_self_correct(self):
        llm = FakeLLM([
            _action("fetch_the_moon"),
            _action("done", {"summary": "recovered"}),
        ])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("go", ToolContext())
        assert result.status == "done"
        assert result.steps[0].ok is False
        assert "unknown tool" in llm.prompts[1]

    def test_final_without_terminal_tool(self):
        llm = FakeLLM([{"thought": "nothing to do", "final": {"summary": "n/a"}}])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("go", ToolContext())
        assert result.status == "final"
        assert result.outcome == {"summary": "n/a"}

    def test_llm_none_reply_means_unavailable(self):
        llm = FakeLLM([None])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("go", ToolContext())
        assert result.status == "llm_unavailable"

    def test_disabled_llm_short_circuits(self):
        llm = FakeLLM([])
        llm.enabled = False
        assert ReactAgent(llm, _echo_registry()).run("go", ToolContext()).status == "llm_unavailable"

    def test_budget_exhaustion(self):
        llm = FakeLLM([_action("echo", {"text": "again"})] * 10)
        result = ReactAgent(llm, _echo_registry(), max_steps=3).run("go", ToolContext())
        assert result.status == "exhausted"
        assert len(result.steps) == 3

    def test_two_protocol_errors_abort(self):
        llm = FakeLLM([{"gibberish": 1}, {"more": 2}])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("go", ToolContext())
        assert result.status == "aborted"
        assert len(result.steps) == 2

    def test_one_protocol_error_then_recovery(self):
        llm = FakeLLM([{"gibberish": 1}, _action("done", {"summary": "ok"})])
        result = ReactAgent(llm, _echo_registry(), max_steps=5).run("go", ToolContext())
        assert result.status == "done"

    def test_tool_timeout_becomes_an_error_observation(self):
        llm = FakeLLM([
            _action("slow"),
            _action("done", {"summary": "gave up on slow"}),
        ])
        result = ReactAgent(llm, _echo_registry(), max_steps=5, step_timeout_sec=0.05).run(
            "go", ToolContext()
        )
        assert result.status == "done"
        assert "timed out" in result.steps[0].observation["error"]

    def test_timed_out_tool_cannot_corrupt_the_live_context(self):
        # A timed-out tool's leaked thread mutates only its orphaned deep-copied
        # snapshot — the live ctx.state must stay untouched even after the thread
        # eventually finishes (the critical staged-plan corruption race).
        started = threading.Event()
        finished = threading.Event()

        def poison(ctx: ToolContext) -> dict[str, Any]:
            started.set()
            time.sleep(0.15)  # outlive the step timeout
            ctx.state["graph"] = {"tasks": ["POISONED"]}
            finished.set()
            return {"ok": True}

        registry = ToolRegistry([
            Tool("poison", "slow mutator", {"type": "object", "properties": {}, "required": []}, poison),
            Tool("done", "terminal",
                 {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                 lambda ctx, summary: (setattr(ctx, "done", True), {"staged": True})[1]),
        ])
        llm = FakeLLM([_action("poison"), _action("done", {"summary": "ok"})])
        ctx = ToolContext(state={"graph": {"tasks": ["original"]}})
        result = ReactAgent(llm, registry, max_steps=5, step_timeout_sec=0.05).run("go", ctx)
        assert started.is_set()
        assert finished.wait(2.0)  # the leaked thread did complete its late write…
        assert ctx.state["graph"] == {"tasks": ["original"]}  # …into the orphan only
        assert result.status == "done"

    def test_completed_tool_commits_its_state(self):
        # The commit-on-success path: a tool that finishes in time publishes its state.
        def write(ctx: ToolContext) -> dict[str, Any]:
            ctx.state["graph"] = {"tasks": ["written"]}
            return {"ok": True}

        registry = ToolRegistry([
            Tool("write", "writes state", {"type": "object", "properties": {}, "required": []}, write),
            Tool("done", "terminal",
                 {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
                 lambda ctx, summary: (setattr(ctx, "done", True), {"staged": True})[1]),
        ])
        llm = FakeLLM([_action("write"), _action("done", {"summary": "ok"})])
        ctx = ToolContext(state={"graph": None})
        assert ReactAgent(llm, registry, max_steps=5).run("go", ctx).status == "done"
        assert ctx.state["graph"] == {"tasks": ["written"]}

    def test_stuck_llm_decision_is_bounded_by_the_step_timeout(self):
        class StuckLLM(FakeLLM):
            def generate_json(self, prompt, system=None):
                time.sleep(0.3)  # a hung provider chain
                return _action("echo", {"text": "late"})

        result = ReactAgent(StuckLLM([]), _echo_registry(), max_steps=5,
                            step_timeout_sec=0.05).run("go", ToolContext())
        assert result.status == "llm_unavailable"
        assert result.steps == []


class TestPlanningRegistry:
    def _registry(self, llm=None, **kw):
        from aeo.agents.runtime import brief_from_dict

        defaults = dict(
            research=lambda brief, **k: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]},
            planner=lambda brief: {
                "topic": "ctem", "coverage_pct": 40,
                "tasks": [
                    {"id": "page:/a", "kind": "content", "title": "Create: A", "priority": 1,
                     "status": "proposed", "node": {"slug": "/a"}},
                    {"id": "page:/b", "kind": "content", "title": "Create: B", "priority": 2,
                     "status": "proposed", "node": {"slug": "/b"}},
                ],
            },
            builder=lambda graph, **k: {
                **graph,
                "tasks": [{**t, "draft": {"h1": t["title"], "sections": []}, "status": "drafted"}
                          for t in graph["tasks"]],
            },
            critic=lambda graph, **k: {
                **graph,
                "tasks": [{**t, "critic": {"needs_review": t["id"] == "page:/b",
                                           "reasons": ["weak claims"]}}
                          for t in graph["tasks"]],
            },
            brief_builder=brief_from_dict,
            llm=llm or FakeLLM([]),
        )
        defaults.update(kw)
        return planning_registry(**defaults)

    def _ctx(self):
        return ToolContext(state={"brief": {"name": "Acme", "domain": "acme.com"}})

    def test_research_folds_competitors_into_the_brief(self):
        ctx = self._ctx()
        obs = self._registry().invoke(ctx, "research_competitors", {})
        assert obs["verified"] == ["rapid7.com"]
        assert ctx.state["brief"]["competitors"] == ["rapid7.com"]

    def test_plan_then_draft_then_critique(self):
        ctx, reg = self._ctx(), self._registry()
        assert reg.invoke(ctx, "plan_pages", {})["task_count"] == 2
        assert reg.invoke(ctx, "draft_pages", {})["drafted"] == 2
        flagged = reg.invoke(ctx, "critique_drafts", {})["flagged"]
        assert [f["id"] for f in flagged] == ["page:/b"]

    def test_draft_before_plan_is_a_polite_error(self):
        obs = self._registry().invoke(self._ctx(), "draft_pages", {})
        assert "plan_pages first" in obs["error"]

    def test_revise_task_can_drop_a_flagged_task(self):
        ctx, reg = self._ctx(), self._registry()
        reg.invoke(ctx, "plan_pages", {})
        obs = reg.invoke(ctx, "revise_task", {"task_id": "page:/b", "drop": True})
        assert obs == {"dropped": "page:/b", "task_count": 1}

    def test_stage_plan_requires_a_plan(self):
        obs = self._registry().invoke(self._ctx(), "stage_plan", {"summary": "premature"})
        assert "empty plan" in obs["error"]

    def test_stage_plan_sets_done(self):
        ctx, reg = self._ctx(), self._registry()
        reg.invoke(ctx, "plan_pages", {})
        obs = reg.invoke(ctx, "stage_plan", {"summary": "two pages planned"})
        assert obs["staged"] is True
        assert ctx.done is True
        assert ctx.outcome["summary"] == "two pages planned"

    def test_semantic_search_degrades_without_pgvector(self, monkeypatch):
        from aeo.storage.repos import embeddings as embeddings_repo

        monkeypatch.setattr(embeddings_repo, "available", lambda: False)
        obs = self._registry().invoke(self._ctx(), "semantic_search", {"query": "ctem"})
        assert "unavailable" in obs["error"]


class _ReactFakeRepo:
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


class TestRuntimeReactMode:
    def _controller(self, repo, llm, cfg=None):
        return AgentRunController(
            research=lambda brief, **k: {"competitors": []},
            planner=lambda brief: {
                "topic": "ctem",
                "tasks": [{"id": "page:/a", "kind": "content", "title": "Create: A",
                           "priority": 1, "status": "proposed", "node": {"slug": "/a"}}],
            },
            builder=lambda graph, **k: graph,
            critic=lambda graph, **k: graph,
            repo=repo,
            llm_provider=lambda: llm,
            cfg=cfg or AgentsCfg(mode="react", react_max_steps=6),
        )

    def _row(self):
        return {"id": "run1", "status": "queued", "brief": {"name": "Acme", "domain": "acme.com"}}

    def test_react_mode_stages_via_the_terminal_tool(self):
        repo = _ReactFakeRepo(self._row())
        llm = FakeLLM([
            _action("plan_pages"),
            _action("stage_plan", {"summary": "planned one page"}),
        ])
        out = self._controller(repo, llm).run("run1")
        assert out["status"] == "staged"
        assert out["result"]["agent_summary"] == "planned one page"
        assert [s["tool"] for s in repo.steps] == ["plan_pages", "stage_plan"]
        assert all(s["agent"] == "react" for s in repo.steps)

    def test_react_collapse_falls_back_to_ladder_with_seq_continuity(self):
        repo = _ReactFakeRepo(self._row())
        llm = FakeLLM([{"junk": 1}, {"junk": 2}])  # protocol collapse after 2 steps
        out = self._controller(repo, llm).run("run1")
        assert out["status"] == "staged"  # the ladder floor still delivered
        seqs = [s["seq"] for s in repo.steps]
        assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))  # no UNIQUE violation
        assert repo.steps[-1]["agent"] in ("planner", "builder", "critic")

    def test_ladder_mode_ignores_react_entirely(self):
        repo = _ReactFakeRepo(self._row())
        llm = FakeLLM([_action("plan_pages")])
        cfg = AgentsCfg(mode="ladder")
        out = self._controller(repo, llm, cfg=cfg).run("run1")
        assert out["status"] == "staged"
        assert llm.prompts == []  # react never consulted the model

    def test_disabled_llm_goes_straight_to_ladder(self):
        repo = _ReactFakeRepo(self._row())
        llm = FakeLLM([])
        llm.enabled = False
        out = self._controller(repo, llm).run("run1")
        assert out["status"] == "staged"
        assert all(s["agent"] != "react" for s in repo.steps)

    def test_redelivery_resumes_seq_after_the_first_attempts_steps(self):
        # At-least-once delivery: a repo exposing max_seq makes the retry continue
        # numbering after the dead attempt's rows instead of colliding on UNIQUE.
        repo = _ReactFakeRepo({**self._row(), "status": "planning"})
        repo.steps = [{"run_id": "run1", "seq": 1, "agent": "react", "tool": "plan_pages"},
                      {"run_id": "run1", "seq": 2, "agent": "react", "tool": "draft_pages"}]
        repo.max_seq = lambda run_id: max((s["seq"] for s in repo.steps), default=0)
        llm = FakeLLM([
            _action("plan_pages"),
            _action("stage_plan", {"summary": "second attempt"}),
        ])
        out = self._controller(repo, llm).run("run1")
        assert out["status"] == "staged"
        seqs = [s["seq"] for s in repo.steps]
        assert seqs == [1, 2, 3, 4]  # no duplicates, resumed after the stale rows

    def test_unexpected_crash_marks_the_run_failed_not_wedged(self):
        repo = _ReactFakeRepo(self._row())

        def exploding_planner(brief):
            raise OSError("db vanished")

        ctrl = AgentRunController(
            research=lambda brief, **k: {"competitors": []},
            planner=exploding_planner,
            builder=lambda graph, **k: graph,
            critic=lambda graph, **k: graph,
            repo=repo,
            llm_provider=lambda: None,  # ladder path
            cfg=AgentsCfg(mode="react", research_enabled=False),
        )
        with pytest.raises(OSError):
            ctrl.run("run1")
        assert repo.runs["run1"]["status"] == "failed"  # terminal, never stuck 'planning'
