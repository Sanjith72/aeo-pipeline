// v5 / Phase 3 item 3.4 — the pack → plan adapter.
//
// Clicking a pack used to open a TicketBoard + PackDetail *below* the pack grid: a second,
// parallel to-do surface with its own layout, its own progress, and its own idea of what
// "done" means. The user therefore had two lists of work with no relationship to each other.
// This maps a pack's tickets into the SAME shape "Your plan" already renders, bucketed into
// the same three phases (Quick Wins → Foundation → Growth & Scale), so there is one list.
//
// Pure — no React, no fetch — so the bucketing rule is unit-tested rather than eyeballed,
// and the build and resume paths derive an identical plan from identical inputs.
//
// What it does NOT do: own status. A mapped task's status still belongs to the ticket, and
// closing one must drive the real ticket close → forced re-crawl → verified_completed flow.
// This module only reshapes; the caller wires the mutations to /api/tickets/*.

import type { PhaseKey } from "./phases";
import type { MilestoneStatus, SkillKey, SkillPriority, Ticket, TicketStatus } from "./types";

/**
 * The roadmap order, restated rather than imported.
 *
 * Not a duplication by preference: source modules resolve imports through the bundler and so
 * carry no file extension, while Node's built-in test runner (which is what makes this
 * module's bucketing rule testable at all) needs an explicit one. A *type* import is erased
 * before Node ever sees it — a *value* import is not, and would make this file unloadable
 * under `node --test`. See the note in web/tsconfig.json.
 *
 * phases.ts remains the single source of truth: packPlan.test.ts imports PHASE_ORDER from it
 * and asserts this array is identical, so the two cannot drift without a test failing.
 */
export const PACK_PHASE_ORDER: readonly PhaseKey[] = ["week_1", "week_2_4", "later"];

/**
 * The DOM id that ties one page×skill fix to its row in "Your plan" (Phase 3 item 3.5).
 *
 * The Pages tab lists a page's fixes and must link to the SAME task in Your plan, but the two
 * surfaces read different endpoints: Pages has SkillPriority (skill + the page's url) while
 * the plan has a Ticket whose task_key is `skill:<skill>@<url_normalized>` — normalised
 * SERVER-side, by a Python helper this bundle has no equivalent of. Reimplementing that
 * normalisation in TypeScript would be a second source of truth that silently rots the day
 * the server's changes.
 *
 * So the anchor is derived from the two fields both sides already hold verbatim. They really
 * are the same string: `generate_tickets_from_run` takes `page_url` straight from
 * `detail_for_pack`, which is exactly what `GET /api/packs/{run}/{pack}` returns as
 * `page.url`. One function, called from both, so they cannot disagree.
 */
export function packFixDomId(skill: string | null | undefined, pageUrl: string | null | undefined): string {
  return `packfix:${skill ?? "?"}@${pageUrl ?? "?"}`;
}

/** A pack ticket wearing the plan's clothes. Keeps the ticket-only fields the plan has no
 *  equivalent for (the 4th status, the before/after scores) rather than flattening them
 *  away — the UI needs "Verifying…" to be distinguishable from "done". */
export interface PackPlanTask {
  task_key: string;
  label: string;
  action_required: string;
  how_to: string;
  /** Mapped into the plan's 3-state vocabulary, for shared rendering. */
  status: MilestoneStatus;
  /** The REAL ticket status. closed_pending_verify has no MilestoneStatus equivalent and is
   *  the whole point of the CH-15 verify loop, so it must survive the mapping. */
  ticketStatus: TicketStatus;
  phase: PhaseKey;
  page_url: string | null;
  skill: SkillKey | null;
  baseline_score: number | null;
  current_score: number | null;
  pack_index: number;
}

export interface PackPlanPhase {
  key: PhaseKey;
  tasks: PackPlanTask[];
}

/**
 * A ticket's status in the plan's vocabulary.
 *
 * ``closed_pending_verify`` maps to ``in_progress``, NOT ``verified_completed``: the owner
 * has done the work but the re-crawl has not confirmed it yet, and claiming it as verified
 * is precisely the dishonesty the CH-15 verify loop exists to prevent. Callers that need to
 * show "Verifying…" read ``ticketStatus``.
 */
export function ticketStatusToMilestoneStatus(status: TicketStatus): MilestoneStatus {
  if (status === "verified_completed") return "verified_completed";
  if (status === "closed_pending_verify" || status === "in_progress") return "in_progress";
  return "pending";
}

/**
 * Which phase a pack fix belongs in.
 *
 * Bucketed by REAL signal, deliberately not by array position — the tickets arrive in pack
 * order, so index-bucketing would put "the first three" in Quick Wins regardless of whether
 * they are quick or winning, and the phase labels would mean nothing.
 *
 * Two signals, in order of trust:
 *
 *  1. ``impact`` from the pack's SkillPriority (CH-06: weight × severity × lift). This is the
 *     product's own ranking of what is worth doing, so when it exists it decides.
 *  2. Otherwise the ticket's ``baseline_score`` — how bad the page currently is on that
 *     skill. A very low score is a big, structural gap (Growth & Scale); a near-passing
 *     score is a small correction (Quick Wins). Note the inversion: a LOW score means MORE
 *     work, so it sorts later, which is the opposite of the impact rule above.
 *
 * Mirrors ``phaseForAction`` in phases.ts in spirit — three bands, cheapest first — so a
 * mapped pack fix and a strategy action land in comparable places.
 */
export function bucketTicket(ticket: Ticket, priority?: SkillPriority | null): PhaseKey {
  const impact = priority?.impact;
  if (typeof impact === "number" && Number.isFinite(impact)) {
    // High impact for low effort is the definition of a quick win; the bands mirror the
    // impact scale CH-06 produces (roughly 0-1, occasionally above on stacked severity).
    if (impact >= 0.6) return "week_1";
    if (impact >= 0.3) return "week_2_4";
    return "later";
  }
  const score = ticket.baseline_score;
  if (typeof score === "number" && Number.isFinite(score)) {
    if (score >= 60) return "week_1"; // nearly there — a small correction
    if (score >= 30) return "week_2_4"; // real work, but the page has a foundation
    return "later"; // little or nothing there yet — a structural build
  }
  // No signal at all. Foundation is the honest middle: calling an unmeasured fix a Quick Win
  // over-promises, and burying it in Growth & Scale hides work the user paid to see.
  return "week_2_4";
}

/** Index a pack's priorities by skill, so a ticket can find its own impact score. Later
 *  duplicates lose — ``detail_for_pack`` emits priorities highest-impact first, so the first
 *  entry for a skill is the one that ranked it. */
export function priorityBySkill(
  priorities: readonly SkillPriority[] | null | undefined,
): Map<SkillKey, SkillPriority> {
  const out = new Map<SkillKey, SkillPriority>();
  for (const p of priorities ?? []) if (!out.has(p.skill)) out.set(p.skill, p);
  return out;
}

/** Map one ticket into the plan's task shape. */
export function ticketToPlanTask(ticket: Ticket, priority?: SkillPriority | null): PackPlanTask {
  return {
    task_key: ticket.task_key,
    label: ticket.label,
    // The ticket's own body is the instruction; fall back to the label rather than an empty
    // string so a task never renders as a bare title with no guidance.
    action_required: ticket.action_required?.trim() || ticket.label,
    how_to: ticket.how_to?.trim() || ticket.action_required?.trim() || "",
    status: ticketStatusToMilestoneStatus(ticket.status),
    ticketStatus: ticket.status,
    phase: bucketTicket(ticket, priority),
    page_url: ticket.page_url,
    skill: ticket.skill,
    baseline_score: ticket.baseline_score,
    current_score: ticket.current_score,
    pack_index: ticket.pack_index,
  };
}

/**
 * A pack's tickets as plan phases, in roadmap order, dropping empty phases.
 *
 * Within a phase, tasks are ordered by how close they are to done and then by page, so the
 * list reads as work rather than as a database dump: unfinished first (there is no value in
 * a verified item sitting at the top of a to-do list), then grouped by page so someone
 * working one page can do all of its fixes in one visit.
 */
export function packPlanPhases(
  tickets: readonly Ticket[] | null | undefined,
  priorities?: readonly SkillPriority[] | null,
): PackPlanPhase[] {
  const bySkill = priorityBySkill(priorities);
  const buckets: Record<PhaseKey, PackPlanTask[]> = { week_1: [], week_2_4: [], later: [] };
  for (const t of tickets ?? []) {
    const task = ticketToPlanTask(t, t.skill ? bySkill.get(t.skill) : null);
    buckets[task.phase].push(task);
  }
  const rank: Record<MilestoneStatus, number> = { pending: 0, in_progress: 1, verified_completed: 2 };
  for (const key of PACK_PHASE_ORDER) {
    buckets[key].sort(
      (a, b) =>
        rank[a.status] - rank[b.status] ||
        (a.page_url ?? "").localeCompare(b.page_url ?? "") ||
        a.task_key.localeCompare(b.task_key),
    );
  }
  return PACK_PHASE_ORDER.filter((k) => buckets[k].length > 0).map((k) => ({ key: k, tasks: buckets[k] }));
}

/** Progress across a pack's mapped tasks, in the same shape the tracker's roll-up uses, so
 *  the pack's numbers and the plan's are computed by the same rule. */
export function packPlanProgress(phases: readonly PackPlanPhase[]): {
  total: number;
  verified: number;
  in_progress: number;
  pct: number;
} {
  const all = phases.flatMap((p) => p.tasks);
  const verified = all.filter((t) => t.status === "verified_completed").length;
  const inProgress = all.filter((t) => t.status === "in_progress").length;
  return {
    total: all.length,
    verified,
    in_progress: inProgress,
    pct: all.length ? Math.round((verified / all.length) * 100) : 0,
  };
}
