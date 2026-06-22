"""AgentRunController — the deterministic controller for one assistive-copilot run.

It is the agent-era sibling of orchestrator.audit_cycle: it sequences typed steps, persists
each to agent_steps, and leaves the run 'staged' for human approval. Every step has a
deterministic floor, so an LLM failure (Phase 2B) degrades to the deterministic result rather
than blocking. Injectable (planner/repo/brief_builder) so the loop is unit-testable with no DB.
"""

from __future__ import annotations

from typing import Any

from ..reference.business_input import BusinessInput
from ..storage.repos import agent_runs as agent_runs_repo
from .planner import plan_tasks

# A run in one of these states is already resolved — re-delivery of its job is a safe no-op.
_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})


def brief_from_dict(d: dict[str, Any]) -> BusinessInput:
    """Rebuild the BusinessInput the run was seeded with (stored on agent_runs.brief)."""
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


class AgentRunController:
    def __init__(self, *, planner=plan_tasks, repo=agent_runs_repo, brief_builder=brief_from_dict) -> None:
        self._planner = planner
        self._repo = repo
        self._brief = brief_builder

    def run(self, run_id: str) -> dict[str, Any]:
        """Drive a run from 'queued' to 'staged'. Idempotent: a run already in a terminal
        state is returned unchanged (safe under at-least-once job delivery)."""
        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        self._repo.set_status(run_id, "planning", current_step="plan")
        brief = self._brief(row.get("brief") or {})
        try:
            graph = self._planner(brief)
        except Exception as exc:
            self._repo.append_step(
                run_id, seq=1, agent="planner", tool="plan_from_brief", status="failed",
                error_class=type(exc).__name__, detail={"error": str(exc)},
            )
            self._repo.set_status(run_id, "failed", error=str(exc))
            raise

        self._repo.append_step(
            run_id, seq=1, agent="planner", tool="plan_from_brief", status="ok",
            detail={"task_count": len(graph.get("tasks", []))},
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
