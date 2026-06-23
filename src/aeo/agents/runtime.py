"""AgentRunController — the deterministic controller for one assistive-copilot run.

It is the agent-era sibling of orchestrator.audit_cycle: it sequences typed steps
(research → plan → build), persists each to agent_steps, and leaves the run 'staged' for
human approval. Every step has a deterministic floor, so an LLM failure degrades to the
deterministic result rather than blocking. Injectable (research/planner/builder/repo/llm)
so the loop is unit-testable with no DB and no network.
"""

from __future__ import annotations

from typing import Any

from ..reference.business_input import BusinessInput
from ..storage.repos import agent_runs as agent_runs_repo
from .builder import build_drafts
from .instrument import InstrumentedLLM
from .planner import plan_tasks
from .research import research_competitors

_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})


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
        repo=agent_runs_repo,
        brief_builder=brief_from_dict,
        llm_provider=_planning_client,
        cfg=None,
    ) -> None:
        self._research = research
        self._planner = planner
        self._builder = builder
        self._repo = repo
        self._brief = brief_builder
        self._llm_provider = llm_provider
        self._cfg = cfg

    def run(self, run_id: str) -> dict[str, Any]:
        """Drive a run research → plan → build → staged. Each step has a deterministic floor;
        idempotent (a terminal run is a no-op under at-least-once delivery)."""
        from ..settings import get_settings

        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        cfg = self._cfg or get_settings().agents
        brief_dict = dict(row.get("brief") or {})
        seq = 0

        # ── research (best-effort; deterministic-first) ──
        if cfg.research_enabled:
            self._repo.set_status(run_id, "planning", current_step="research")
            try:
                res = self._research(brief_dict, llm=self._llm_provider())
            except Exception:  # research never blocks a run
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
            origin = f"https://{brief.key()}" if brief.domain else None
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
