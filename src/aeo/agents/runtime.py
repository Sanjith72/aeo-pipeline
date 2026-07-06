"""AgentRunController — one assistive-copilot run, agentic-first.

Two orchestration modes (``AEO__AGENTS__MODE``):

  - ``react`` (default): the agentic loop. The planning-tier LLM drives the tool
    registry (agents/tools.py) — it decides whether to research competitors, when to
    plan, how much to draft, how to react to the critic's flags (revise/drop/redraft),
    and stages the plan itself via the terminal ``stage_plan`` tool. Every step is
    persisted to agent_steps with thought/args/observation for the ``aeo trace`` view.
  - ``ladder``: the fixed research → plan → build → critic sequence — and the automatic
    fallback whenever no LLM is available or the loop fails to stage, preserving the
    deterministic-first contract: an LLM outage degrades, it never blocks a run.

Both modes end 'staged'; only a human approve/reject advances a run. Injectable
(research/planner/builder/repo/llm) so both loops are unit-testable with no DB/network.
"""

from __future__ import annotations

import json
from typing import Any

from ..logging import get_logger
from ..reference.business_input import BusinessInput
from ..storage.repos import agent_runs as agent_runs_repo
from .builder import build_drafts
from .critic import review_drafts
from .instrument import InstrumentedLLM
from .planner import plan_tasks
from .react import ReactAgent, StepTrace
from .research import research_competitors
from .tools import ToolContext, planning_registry

log = get_logger(__name__)

_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})

_PLANNING_GOAL = """\
Produce the best possible AEO content plan for this business and stage it for human
review.

BUSINESS BRIEF:
{brief}

Work agentically: ground yourself first (research competitors if none are known;
inventory the existing site if the brief has a domain), then plan the pages, draft the
top-priority ones, run the critic over the drafts, and act on its flags (revise, drop,
or accept with reason). Finish by calling stage_plan with a reviewer-facing summary.
"""


def brief_from_dict(d: dict[str, Any]) -> BusinessInput:
    return BusinessInput(
        name=d.get("name") or d.get("domain") or "site",
        domain=d.get("domain"),
        category=d.get("category"),
        topic=d.get("topic"),
        location=d.get("location"),
        services=list(d.get("services") or []),
        competitors=list(d.get("competitors") or []),
        goals=list(d.get("goals") or []),
    )


def _planning_client():
    from ..nlp.llm import get_planning_client

    return get_planning_client()


class AgentRunController:
    def __init__(
        self,
        *,
        research=research_competitors,
        planner=plan_tasks,
        builder=build_drafts,
        critic=review_drafts,
        repo=agent_runs_repo,
        brief_builder=brief_from_dict,
        llm_provider=_planning_client,
        cfg=None,
    ) -> None:
        self._research = research
        self._planner = planner
        self._builder = builder
        self._critic = critic
        self._repo = repo
        self._brief = brief_builder
        self._llm_provider = llm_provider
        self._cfg = cfg

    def run(self, run_id: str) -> dict[str, Any]:
        """Agentic loop first (mode="react"), fixed ladder as the deterministic floor.
        Idempotent (a terminal run is a no-op under at-least-once delivery)."""
        from ..settings import get_settings

        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        cfg = self._cfg or get_settings().agents
        vcfg = get_settings().validation

        # At-least-once redelivery: a prior attempt may have persisted steps before the
        # worker died — resume seq numbering after them (UNIQUE(run_id, seq)).
        max_seq = getattr(self._repo, "max_seq", None)
        seq_start = int(max_seq(run_id)) if callable(max_seq) else 0

        try:
            if getattr(cfg, "mode", "ladder") == "react":
                staged, react_steps = self._run_react(run_id, row, cfg, vcfg, seq_start=seq_start)
                if staged is not None:
                    return staged
                seq_start += react_steps
                log.info("agent_react_fell_back_to_ladder", run=run_id, steps_used=react_steps)

            return self._run_ladder(run_id, row, cfg, vcfg, seq_start=seq_start)
        except Exception as exc:
            # Whatever escapes, the run must land in a terminal state — never wedge in
            # 'planning' with the UI polling a corpse. (The ladder already marks known
            # failures; set_status only fills fields that are provided, so this is a
            # safe catch-all for the rest.)
            row_now = self._repo.get(run_id)
            if row_now is not None and row_now.get("status") not in _TERMINAL:
                self._repo.set_status(run_id, "failed", error=str(exc))
            raise

    def _run_react(
        self, run_id: str, row: dict[str, Any], cfg, vcfg, *, seq_start: int = 0
    ) -> tuple[dict[str, Any] | None, int]:
        """Drive the agentic loop. Returns ``(staged_row, steps_used)`` — ``staged_row``
        is ``None`` when the loop couldn't finish (no LLM, protocol collapse, budget
        exhausted, empty plan) and the caller should fall back to the ladder, continuing
        agent_steps numbering after this attempt's steps."""
        client = self._llm_provider()
        if client is None or not getattr(client, "enabled", False):
            return None, 0

        inst = InstrumentedLLM(client)
        ctx = ToolContext(state={"brief": dict(row.get("brief") or {})})
        registry = planning_registry(
            research=self._research, planner=self._planner, builder=self._builder,
            critic=self._critic, brief_builder=self._brief, llm=inst,
            draft_limit=cfg.draft_limit, verify_citations=vcfg.verify_citations,
            adversarial_max_attempts=vcfg.adversarial_max_attempts,
        )

        calls_seen = 0

        def persist(trace: StepTrace) -> None:
            # Per-step cost = the InstrumentedLLM calls this step added.
            nonlocal calls_seen
            new_calls = inst.calls[calls_seen:]
            calls_seen = len(inst.calls)
            self._repo.append_step(
                run_id, seq=seq_start + trace.seq, agent="react", tool=trace.tool or "reason",
                status="ok" if trace.ok else "failed",
                model=inst.model,
                tokens=sum(c.tokens for c in new_calls) or None,
                cost_usd=round(sum(c.cost_usd for c in new_calls), 6) if new_calls else None,
                latency_ms=trace.latency_ms,
                detail={
                    "thought": trace.thought[:500],
                    "args": trace.args,
                    "observation": json.loads(json.dumps(trace.observation, default=str))
                    if trace.observation else {},
                },
            )

        self._repo.set_status(run_id, "planning", current_step="react")
        agent = ReactAgent(
            inst, registry,
            max_steps=cfg.react_max_steps, step_timeout_sec=cfg.step_timeout_sec,
            on_step=persist,
        )
        result = agent.run(_PLANNING_GOAL.format(brief=json.dumps(ctx.state["brief"], default=str)), ctx)

        graph = ctx.state.get("graph")
        if result.status == "done" and graph and graph.get("tasks"):
            if result.outcome and result.outcome.get("summary"):
                graph = {**graph, "agent_summary": result.outcome["summary"]}
            self._repo.set_status(run_id, "staged", current_step="review", result=graph)
            return self._repo.get(run_id), len(result.steps)
        log.warning("agent_react_incomplete", run=run_id, status=result.status,
                    steps=len(result.steps), has_graph=bool(graph))
        return None, len(result.steps)

    def _run_ladder(
        self, run_id: str, row: dict[str, Any], cfg, vcfg, *, seq_start: int = 0
    ) -> dict[str, Any]:
        """research → plan → build → critic → staged — the fixed sequence and the
        deterministic floor under the react mode."""
        brief_dict = dict(row.get("brief") or {})
        seq = seq_start

        # ── research (best-effort; deterministic-first) ──
        if cfg.research_enabled:
            self._repo.set_status(run_id, "planning", current_step="research")
            try:
                res = self._research(brief_dict, llm=self._llm_provider())
            except Exception:
                res = {"competitors": []}
            competitors = res.get("competitors") or []
            if competitors:
                brief_dict = {**brief_dict, "competitors": [c["domain"] or c["name"] for c in competitors]}
            seq += 1
            self._repo.append_step(
                run_id, seq=seq, agent="research", tool="discover_competitors", status="ok",
                detail={"verified": len(competitors)},
            )

        # ── plan (deterministic) ──
        self._repo.set_status(run_id, "planning", current_step="plan")
        brief = self._brief(brief_dict)
        origin = f"https://{brief.key()}" if brief.domain else None
        seq += 1
        try:
            graph = self._planner(brief)
        except Exception as exc:
            self._repo.append_step(
                run_id, seq=seq, agent="planner", tool="plan_from_brief", status="failed",
                error_class=type(exc).__name__, detail={"error": str(exc)},
            )
            self._repo.set_status(run_id, "failed", error=str(exc))
            raise
        self._repo.append_step(
            run_id, seq=seq, agent="planner", tool="plan_from_brief", status="ok",
            detail={"task_count": len(graph.get("tasks", []))},
        )

        # ── build (deterministic floor; cost recorded) ──
        if cfg.build_enabled:
            self._repo.set_status(run_id, "planning", current_step="build")
            client = self._llm_provider()
            inst = InstrumentedLLM(client) if client is not None else None
            seq += 1
            try:
                graph = self._builder(graph, llm=inst, origin=origin, limit=cfg.draft_limit)
            except Exception as exc:
                self._repo.append_step(
                    run_id, seq=seq, agent="builder", tool="draft_missing_page", status="failed",
                    error_class=type(exc).__name__, detail={"error": str(exc)},
                )
                self._repo.set_status(run_id, "failed", error=str(exc))
                raise
            drafted = sum(1 for t in graph.get("tasks", []) if t.get("draft"))
            totals = inst.totals() if inst else {"tokens": None, "cost_usd": None, "llm_calls": 0}
            self._repo.append_step(
                run_id, seq=seq, agent="builder", tool="draft_missing_page", status="ok",
                model=(inst.model if inst else None),
                tokens=totals["tokens"], cost_usd=totals["cost_usd"],
                detail={"drafts": drafted, "llm_calls": totals["llm_calls"]},
            )

        # ── critic (model-isolated gate; deterministic floor; cost recorded) ──
        if cfg.critic_enabled:
            self._repo.set_status(run_id, "planning", current_step="critic")
            client = self._llm_provider()
            inst = InstrumentedLLM(client) if client is not None else None
            seq += 1
            try:
                graph = self._critic(
                    graph, llm=inst, origin=origin,
                    verify_citations=vcfg.verify_citations,
                    adversarial_max_attempts=vcfg.adversarial_max_attempts,
                )
            except Exception as exc:
                self._repo.append_step(
                    run_id, seq=seq, agent="critic", tool="adversarial_audit", status="failed",
                    error_class=type(exc).__name__, detail={"error": str(exc)},
                )
                self._repo.set_status(run_id, "failed", error=str(exc))
                raise
            reviewed = sum(1 for t in graph.get("tasks", []) if t.get("critic"))
            flagged = sum(1 for t in graph.get("tasks", []) if t.get("critic", {}).get("needs_review"))
            totals = inst.totals() if inst else {"tokens": None, "cost_usd": None, "llm_calls": 0}
            self._repo.append_step(
                run_id, seq=seq, agent="critic", tool="adversarial_audit", status="ok",
                model=(inst.model if inst else None),
                tokens=totals["tokens"], cost_usd=totals["cost_usd"],
                detail={"reviewed": reviewed, "flagged": flagged, "llm_calls": totals["llm_calls"]},
            )

        self._repo.set_status(run_id, "staged", current_step="review", result=graph)
        return self._repo.get(run_id)


def start_agent_run(brief: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
    """Create a run row and enqueue it on the Postgres job queue for a worker to drive.
    Used by the API and CLI. The worker picks it up via the AGENT_RUN job kind."""
    from ..pipeline.worker import enqueue_agent_run  # lazy: avoid import cycle with worker

    row = agent_runs_repo.create(
        idempotency_key=idempotency_key, domain=brief.get("domain"), client_id=None, brief=brief
    )
    enqueue_agent_run(row["id"])
    return row
