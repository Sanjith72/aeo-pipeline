// web/lib/quest/model.ts
// Pure projection from the real tracker data (StructuredPlan + MilestoneDashboard) into the
// Quest Map view model. No I/O, no React — fully unit-testable. The join key is
// MilestoneTask.task_key === PlanTask.id; phase order/grouping comes from the milestones
// (milestone_key === plan phase key), impact/prompts from the plan.

import type { MilestoneDashboard, PlanTask, StructuredPlan } from "../types";
import type { NodeState, QuestEnemy, QuestModel, QuestPhase, QuestTask } from "./types";

const COIN_BASE = 50;
const COIN_RANGE = 150;

/** The reference enemy scale every theme provides (weakest → final boss). The model is
 *  theme-agnostic: it picks a slot in [0, ENEMY_STEPS-1]; the theme supplies the art. */
export const ENEMY_STEPS = 6;

const clamp01 = (n: number): number => (n < 0 ? 0 : n > 1 ? 1 : n);

/** Coins a task drops on completion — a handful for a low-impact task, a big burst for a
 *  high-impact one. This (plus enemy size) is how impact is read at a glance. */
export function coinsForImpact(impact: number): number {
  return Math.round(COIN_BASE + clamp01(impact) * COIN_RANGE);
}

/** Glyph scale for an enemy: impact nudges it ±~15%; bosses are inherently larger. */
export function enemySize(impact: number, isBoss: boolean): number {
  const base = 0.85 + clamp01(impact) * 0.3; // 0.85 – 1.15
  const scaled = isBoss ? Math.max(base, 1.0) * 1.35 : base;
  return Math.round(scaled * 100) / 100;
}

/** Map a task's position within its phase onto the reference enemy scale. The first task is
 *  always the weakest (slot 0) and the **last task is always the final boss**, regardless of
 *  count; middle tasks compress (few tasks) or repeat mid-tiers (many). */
export function assignEnemy(index: number, count: number): QuestEnemy {
  const lastSlot = ENEMY_STEPS - 1;
  const isBoss = index === count - 1;
  let slot: number;
  if (isBoss || count <= 1) slot = lastSlot;
  else slot = Math.round((index / (count - 1)) * lastSlot);
  // Reserve the boss slot strictly for the final task.
  if (!isBoss && slot >= lastSlot) slot = lastSlot - 1;
  if (slot < 0) slot = 0;
  return { slot, isBoss };
}

/** The phase-completion payout — always larger than any single task's coins, so the chest
 *  finale feels like the big moment. */
export function chestBonusFor(taskCoins: number[]): number {
  const sum = taskCoins.reduce((s, c) => s + c, 0);
  const max = taskCoins.reduce((m, c) => Math.max(m, c), 0);
  return Math.max(150, max + 50, Math.round(sum * 0.5));
}

/** Coins bank only on a trustworthy completion: the crawl saw it live, or the task is
 *  off-site (verify_kind "manual") where self-report is the only signal there will ever be —
 *  milestone_verify.py never auto-verifies those. A crawlable task that's merely marked stays
 *  pending: it still progresses the phase, but its coins wait for "Check my site". */
export function banked(
  task: Pick<QuestTask, "status" | "statusSource"> & {
    milestoneTask: Pick<QuestTask["milestoneTask"], "verify_kind">;
  },
): boolean {
  if (task.status !== "verified_completed") return false;
  return task.statusSource === "crawl" || task.milestoneTask.verify_kind === "manual";
}

export function buildQuestModel(
  plan: StructuredPlan | null | undefined,
  dashboard: MilestoneDashboard | null | undefined,
): QuestModel {
  const planById = new Map<string, PlanTask>();
  for (const ph of plan?.phases ?? []) for (const t of ph.tasks) planById.set(t.id, t);

  const milestones = [...(dashboard?.milestones ?? [])].sort((a, b) => a.position - b.position);

  const phases: QuestPhase[] = milestones.map((m, mi) => {
    const ordered = m.tasks; // already in position order from the API
    const count = ordered.length;

    const tasks: QuestTask[] = ordered.map((mt, i) => {
      const pt = planById.get(mt.task_key);
      const impact = clamp01(pt?.impact_score ?? 0.5);
      const enemy = assignEnemy(i, count);
      const completed = mt.status === "verified_completed";
      const statusSource = mt.status_source ?? null;
      return {
        id: mt.task_key,
        label: mt.label,
        actionRequired: mt.action_required,
        impact,
        category: mt.task_key.startsWith("vis:") ? "visibility" : "content",
        status: mt.status,
        statusSource,
        verifiedLive: completed && mt.status_source === "crawl",
        coinsBanked: banked({ status: mt.status, statusSource, milestoneTask: mt }),
        index: i,
        enemy,
        enemySize: enemySize(impact, enemy.isBoss),
        coins: coinsForImpact(impact),
        nodeState: "locked" as NodeState, // resolved in the lock/active pass below
        milestoneTask: mt,
        planTask: pt,
      };
    });

    const completed = tasks.filter((t) => t.status === "verified_completed").length;
    const total = count;
    const chestBonus = chestBonusFor(tasks.map((t) => t.coins));
    return {
      key: m.milestone_key,
      index: mi,
      // Raw server titles — the display layer (PhaseTab / MilestoneCard) maps known phase
      // keys onto the shared Quick Wins / Foundation / Growth & Scale names (lib/phases),
      // kept out of here so this module stays runnable under node --test.
      title: m.title,
      blurb: m.blurb,
      tasks,
      completed,
      total,
      pct: total ? Math.round((completed / total) * 100) : 0,
      coinsEarned: 0, // resolved below
      chestBonus,
      locked: false, // resolved below
      // A zero-task phase is vacuously complete, so it can never deadlock the lock cascade.
      isComplete: total === 0 || completed === total,
    };
  });

  // Lock pass: a phase is interactive only once every earlier phase is 100%. Earlier-earned
  // progress in a still-locked phase stays visible (completed enemies render defeated).
  let allPriorComplete = true;
  for (const ph of phases) {
    ph.locked = !allPriorComplete;
    if (!ph.isComplete) allPriorComplete = false;
  }

  // Node-state + banked-coins pass (needs the lock result). Progression (nodeState, unlock,
  // isComplete) stays on mark-complete; only the coin math is gated on banking.
  for (const ph of phases) {
    const activeIndex = ph.locked
      ? -1
      : ph.tasks.findIndex((t) => t.status !== "verified_completed");
    ph.tasks.forEach((t, i) => {
      t.nodeState =
        t.status === "verified_completed" ? "completed" : i === activeIndex ? "active" : "locked";
    });
    const bankedCoins = ph.tasks.filter((t) => t.coinsBanked).reduce((s, t) => s + t.coins, 0);
    // The chest pays out only once every task in the phase has banked — stricter than
    // isComplete, which a merely-marked crawlable task already satisfies.
    const chestBanked = ph.tasks.every((t) => t.coinsBanked);
    ph.coinsEarned = bankedCoins + (chestBanked ? ph.chestBonus : 0);
  }

  const maxCoins = phases.reduce(
    (s, p) => s + p.chestBonus + p.tasks.reduce((s2, t) => s2 + t.coins, 0),
    0,
  );
  return { phases, globalCoins: phases.reduce((s, p) => s + p.coinsEarned, 0), maxCoins };
}
