// The one place the plan's three phases are named and ordered for display, plus the
// Roadmap↔Strategy merge helper (the old Roadmap tab's "big moves" deduped against the
// plan's tracked tasks). Pure — no React, no I/O — so the merge is unit-testable and the
// build and resume paths derive the identical Strategy list from the same inputs.

import type { StrategyAction, StructuredPlan } from "./types";

export type PhaseKey = "week_1" | "week_2_4" | "later";

export const PHASE_ORDER: PhaseKey[] = ["week_1", "week_2_4", "later"];

// Owner-facing phase names, shared by the gamified Roadmap and the Strategy list so the
// two tabs always tell one story. Server milestone titles (aeo.report.milestones) mirror
// these; this map wins on read so stale DB titles can't leak into the UI.
export const PHASE_DISPLAY: Record<PhaseKey, { title: string; blurb: string }> = {
  week_1: { title: "Quick Wins", blurb: "Fast, high-leverage fixes — do these first." },
  week_2_4: { title: "Foundation", blurb: "Make your core pages solid, complete, and trustworthy." },
  later: { title: "Growth & Scale", blurb: "Longer-term moves that compound over time." },
};

export function phaseDisplayTitle(key: string, fallback: string): string {
  return PHASE_DISPLAY[key as PhaseKey]?.title ?? fallback;
}

export function phaseDisplayBlurb(key: string, fallback: string): string {
  return PHASE_DISPLAY[key as PhaseKey]?.blurb ?? fallback;
}

/** Which phase a profile "big move" belongs to — same effort→phase mapping the old
 *  Roadmap tab used (low effort = quick win, medium = foundation, high = growth/scale). */
export function phaseForAction(action: StrategyAction): PhaseKey {
  if (action.effort === "low") return "week_1";
  if (action.effort === "medium") return "week_2_4";
  return "later";
}

const STOPWORDS = new Set(["a", "an", "the", "your", "our", "to", "for", "of", "and", "on", "in", "with", "my", "up"]);

function tokens(s: string): Set<string> {
  return new Set(
    s
      .toLowerCase()
      .replace(/[^a-z0-9\s]+/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 1 && !STOPWORDS.has(w)),
  );
}

function overlap(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 || b.size === 0) return 0;
  let hit = 0;
  for (const w of a) if (b.has(w)) hit += 1;
  return hit / Math.min(a.size, b.size);
}

/** The Roadmap↔Strategy dedupe: drop every profile action the plan already tracks as a
 *  concrete task (the task is the more actionable version — it has a how-to, a dev brief,
 *  and crawl verification). A match is either a related slug the plan tracks as a page
 *  task, or a strong word overlap between the action title and a task label/action. */
export function dedupeActionsAgainstPlan(
  actions: StrategyAction[],
  plan: StructuredPlan | null | undefined,
): StrategyAction[] {
  const tasks = plan?.phases.flatMap((p) => p.tasks) ?? [];
  if (tasks.length === 0) return [...actions].sort((a, b) => a.priority - b.priority);
  const taskIds = new Set(tasks.map((t) => t.id));
  const taskTokens = tasks.map((t) => tokens(`${t.label} ${t.action_required}`));

  return actions
    .filter((a) => {
      if ((a.related_slugs ?? []).some((slug) => taskIds.has(`page:${slug}`))) return false;
      const at = tokens(a.title);
      return !taskTokens.some((tt) => overlap(at, tt) >= 0.6);
    })
    .sort((x, y) => x.priority - y.priority);
}

/**
 * What the "Bigger strategic moves" panel should render right now.
 *
 * Exists because moving that panel to the Overview tab (Phase 3 item 3.3) exposed a trap:
 * Overview can paint long before the plan is built, and ``dedupeActionsAgainstPlan`` returns
 * every action UNFILTERED when there are no tasks to compare against (see the early return
 * above — correct for its own contract, wrong as a render input). Rendering that would show
 * the raw audit list, and then silently drop entries a few seconds later as the plan arrives
 * and the dedupe starts biting. The user would watch items vanish with no explanation.
 *
 * So the "we cannot dedupe yet" case gets its own state and its own copy, instead of being
 * quietly rendered as if it were the final answer. Pure, so the rule is unit-tested once
 * rather than re-derived at each call site.
 */
export type StrategyExtrasState =
  | { kind: "ready"; actions: StrategyAction[] }
  /** There ARE actions, but no plan yet — deduping is impossible, so say so. */
  | { kind: "pending" }
  /** Nothing to show: no actions at all, or none survived the dedupe. */
  | { kind: "empty" };

export function strategyExtrasState(
  actions: StrategyAction[] | null | undefined,
  plan: StructuredPlan | null | undefined,
): StrategyExtrasState {
  const all = actions ?? [];
  if (all.length === 0) return { kind: "empty" };
  // A plan with zero tasks is indistinguishable from no plan for dedupe purposes, and both
  // make the filter a no-op — treat them the same rather than showing an unfiltered list.
  const taskCount = plan?.phases?.flatMap((p) => p.tasks).length ?? 0;
  if (taskCount === 0) return { kind: "pending" };
  const deduped = dedupeActionsAgainstPlan(all, plan);
  return deduped.length === 0 ? { kind: "empty" } : { kind: "ready", actions: deduped };
}

/** Group deduped actions by their phase, in roadmap order, dropping empty phases. */
export function groupActionsByPhase(actions: StrategyAction[]): { key: PhaseKey; actions: StrategyAction[] }[] {
  const buckets: Record<PhaseKey, StrategyAction[]> = { week_1: [], week_2_4: [], later: [] };
  for (const a of actions) buckets[phaseForAction(a)].push(a);
  return PHASE_ORDER.filter((k) => buckets[k].length > 0).map((k) => ({ key: k, actions: buckets[k] }));
}
