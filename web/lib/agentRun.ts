// web/lib/agentRun.ts
// Pure helpers for rendering an agent run in the review queue — no I/O, unit-testable.

import type { AgentRunDetail } from "./types";

export interface RunSummary {
  isStaged: boolean;
  taskCount: number;
  draftedCount: number;
  flaggedCount: number;
  costUsd: number;
}

export function summarizeRun(run: AgentRunDetail): RunSummary {
  const tasks = run.result?.tasks ?? [];
  const steps = run.steps ?? [];
  return {
    isStaged: run.status === "staged",
    taskCount: tasks.length,
    draftedCount: tasks.filter((t) => !!t.draft).length,
    flaggedCount: tasks.filter((t) => t.critic?.needs_review).length,
    costUsd: steps.reduce((sum, s) => sum + (s.cost_usd ?? 0), 0),
  };
}
