// web/lib/quest/types.ts
// Shape of the gamified "Quest Map" view — a pure projection of the milestone tracker.
// Built by lib/quest/model.buildQuestModel from a StructuredPlan + MilestoneDashboard.

import type { MilestoneStatus, MilestoneTask, PlanTask } from "../types";

export type NodeState = "locked" | "active" | "completed";

/** The enemy a task renders as — theme-agnostic. `slot` indexes the theme's ordered roster
 *  (0 = weakest … last = boss); the component resolves the glyph/name via theme.enemyAt.
 *  First task in a phase = slot 0, last task = the named final boss, always. */
export interface QuestEnemy {
  slot: number;
  isBoss: boolean;
}

export interface QuestTask {
  id: string; // === PlanTask.id === MilestoneTask.task_key
  label: string;
  actionRequired: string;
  /** 0–1 expected impact (backend impact_score; 0.5 fallback if a task is unmatched). */
  impact: number;
  category: string; // "content" | "visibility"
  // status, lifted from the milestone record
  status: MilestoneStatus;
  statusSource: "manual" | "crawl" | null;
  /** completed AND detected live by the verification crawl — earns the honest flourish. */
  verifiedLive: boolean;
  // derived presentation
  index: number; // position within the phase (0-based)
  enemy: QuestEnemy;
  /** scale multiplier for the enemy glyph, ~0.85–1.5 (impact + boss bump). */
  enemySize: number;
  coins: number; // reward dropped on completion
  nodeState: NodeState;
  // underlying records, for the detail panel + how-to guide
  milestoneTask: MilestoneTask;
  planTask?: PlanTask;
}

export interface QuestPhase {
  key: string; // week_1 | week_2_4 | later
  index: number; // 0 | 1 | 2
  title: string;
  blurb: string;
  tasks: QuestTask[];
  completed: number;
  total: number;
  pct: number; // 0–100, linear (completed / total)
  coinsEarned: number; // completed-task coins (+ chest bonus once complete)
  chestBonus: number; // the phase-completion payout (always > any single task's coins)
  locked: boolean; // preview-only until every earlier phase is 100%
  isComplete: boolean;
}

export interface QuestModel {
  phases: QuestPhase[];
  globalCoins: number;
}
