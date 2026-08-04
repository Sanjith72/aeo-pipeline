// Unit tests for the pack → plan adapter (lib/packPlan.ts). Runs on Node's built-in test
// runner with native TS type-stripping:
//
//   node --test lib/packPlan.test.ts        (or: npm test, from web/)
//
// The rule under test is the BUCKETING. Pack tickets arrive in pack order, so bucketing by
// array position would drop "the first three" into Quick Wins whether or not they are quick
// or winning — and the phase headings would become decoration. These pin the real signals.

import test from "node:test";
import assert from "node:assert/strict";

import {
  PACK_PHASE_ORDER,
  bucketTicket,
  packFixDomId,
  packPlanPhases,
  packPlanProgress,
  priorityBySkill,
  ticketStatusToMilestoneStatus,
  ticketToPlanTask,
} from "./packPlan.ts";
import { PHASE_ORDER } from "./phases.ts";
import type { SkillPriority, Ticket } from "./types.ts";

test("the pack phase order is IDENTICAL to the plan's", () => {
  // packPlan.ts restates the order instead of importing it: a value import from a sibling
  // has no file extension (bundler resolution) and Node's test runner cannot resolve that,
  // which would make this whole module untestable. phases.ts stays the source of truth and
  // this assertion is what keeps the restatement honest.
  assert.deepEqual([...PACK_PHASE_ORDER], [...PHASE_ORDER]);
});

function ticket(over: Partial<Ticket> = {}): Ticket {
  return {
    id: 1,
    task_key: "skill:messaging@https://x.com/",
    label: "Messaging — /",
    action_required: "Say what you do above the fold.",
    how_to: "Rewrite the hero.",
    status: "pending",
    status_source: "manual",
    detected_at: null,
    pack_index: 1,
    assignee: null,
    target_date: null,
    page_url: "https://x.com/",
    skill: "messaging",
    baseline_score: 50,
    current_score: null,
    closed_at: null,
    ...over,
  };
}

function priority(over: Partial<SkillPriority> = {}): SkillPriority {
  return {
    skill: "messaging",
    text: "Tighten the hero",
    criterion: null,
    skill_score: 50,
    impact: 0.5,
    lift: 0.4,
    lift_basis: "headroom",
    ...over,
  };
}

// ── status mapping ─────────────────────────────────────────────────────────────────

test("closed_pending_verify is IN PROGRESS, never verified", () => {
  // The owner has done the work but the re-crawl has not confirmed it. Calling that
  // "verified" is exactly the dishonesty the CH-15 verify loop exists to prevent.
  assert.equal(ticketStatusToMilestoneStatus("closed_pending_verify"), "in_progress");
  assert.equal(ticketStatusToMilestoneStatus("verified_completed"), "verified_completed");
  assert.equal(ticketStatusToMilestoneStatus("in_progress"), "in_progress");
  assert.equal(ticketStatusToMilestoneStatus("pending"), "pending");
});

test("the real ticket status survives the mapping", () => {
  // Otherwise the UI cannot tell "Verifying…" from "in progress", and the CH-15 loop
  // becomes invisible to the user waiting on it.
  const t = ticketToPlanTask(ticket({ status: "closed_pending_verify" }));
  assert.equal(t.status, "in_progress");
  assert.equal(t.ticketStatus, "closed_pending_verify");
});

// ── bucketing: impact wins when present ────────────────────────────────────────────

test("impact decides the phase when a priority exists", () => {
  const t = ticket({ baseline_score: 5 }); // score would say "later" — impact must override
  assert.equal(bucketTicket(t, priority({ impact: 0.9 })), "week_1");
  assert.equal(bucketTicket(t, priority({ impact: 0.45 })), "week_2_4");
  assert.equal(bucketTicket(t, priority({ impact: 0.1 })), "later");
});

test("impact band edges are inclusive at the bottom", () => {
  assert.equal(bucketTicket(ticket(), priority({ impact: 0.6 })), "week_1");
  assert.equal(bucketTicket(ticket(), priority({ impact: 0.3 })), "week_2_4");
  assert.equal(bucketTicket(ticket(), priority({ impact: 0.299 })), "later");
});

// ── bucketing: baseline score is the fallback, and it INVERTS ──────────────────────

test("without a priority, a nearly-passing page is a Quick Win", () => {
  assert.equal(bucketTicket(ticket({ baseline_score: 75 })), "week_1");
});

test("without a priority, a very low score is structural work", () => {
  // The inversion is the easy thing to get backwards: a LOW score means MORE work, so it
  // sorts LATER — the opposite direction to the impact rule above.
  assert.equal(bucketTicket(ticket({ baseline_score: 10 })), "later");
  assert.equal(bucketTicket(ticket({ baseline_score: 45 })), "week_2_4");
});

test("score band edges", () => {
  assert.equal(bucketTicket(ticket({ baseline_score: 60 })), "week_1");
  assert.equal(bucketTicket(ticket({ baseline_score: 59 })), "week_2_4");
  assert.equal(bucketTicket(ticket({ baseline_score: 30 })), "week_2_4");
  assert.equal(bucketTicket(ticket({ baseline_score: 29 })), "later");
});

test("no signal at all lands in Foundation, not Quick Wins", () => {
  // Calling an unmeasured fix a Quick Win over-promises; burying it hides work the user
  // paid to see. The honest middle.
  assert.equal(bucketTicket(ticket({ baseline_score: null })), "week_2_4");
  assert.equal(bucketTicket(ticket({ baseline_score: null }), null), "week_2_4");
});

test("a non-finite impact falls through to the score rather than throwing", () => {
  assert.equal(bucketTicket(ticket({ baseline_score: 75 }), priority({ impact: NaN })), "week_1");
});

// ── bucketing is NOT positional ────────────────────────────────────────────────────

test("phase does not depend on array position", () => {
  const tickets = [
    ticket({ task_key: "a", baseline_score: 10 }), // first, but structural
    ticket({ task_key: "b", baseline_score: 90 }), // last, but a quick win
  ];
  const phases = packPlanPhases(tickets);
  const quick = phases.find((p) => p.key === "week_1");
  assert.deepEqual(quick?.tasks.map((t) => t.task_key), ["b"]);
  assert.deepEqual(phases.find((p) => p.key === "later")?.tasks.map((t) => t.task_key), ["a"]);
});

// ── grouping and ordering ──────────────────────────────────────────────────────────

test("phases come back in roadmap order with empties dropped", () => {
  const phases = packPlanPhases([
    ticket({ task_key: "late", baseline_score: 5 }),
    ticket({ task_key: "quick", baseline_score: 95 }),
  ]);
  assert.deepEqual(phases.map((p) => p.key), ["week_1", "later"]);
});

test("unfinished work sorts above verified work inside a phase", () => {
  const phases = packPlanPhases([
    ticket({ task_key: "done", baseline_score: 80, status: "verified_completed", page_url: "https://x.com/a" }),
    ticket({ task_key: "todo", baseline_score: 80, status: "pending", page_url: "https://x.com/b" }),
  ]);
  assert.deepEqual(phases[0].tasks.map((t) => t.task_key), ["todo", "done"]);
});

test("within a status, tasks group by page so one visit fixes one page", () => {
  const phases = packPlanPhases([
    ticket({ task_key: "b", baseline_score: 80, page_url: "https://x.com/b" }),
    ticket({ task_key: "a2", baseline_score: 80, page_url: "https://x.com/a" }),
    ticket({ task_key: "a1", baseline_score: 80, page_url: "https://x.com/a" }),
  ]);
  assert.deepEqual(phases[0].tasks.map((t) => t.task_key), ["a1", "a2", "b"]);
});

test("an empty or missing ticket list is an empty plan, not a crash", () => {
  assert.deepEqual(packPlanPhases([]), []);
  assert.deepEqual(packPlanPhases(null), []);
  assert.deepEqual(packPlanPhases(undefined), []);
});

// ── priority lookup ────────────────────────────────────────────────────────────────

test("the highest-ranked priority per skill wins", () => {
  // detail_for_pack emits priorities highest-impact first, so the FIRST entry for a skill is
  // the one that ranked it; a later duplicate must not overwrite it.
  const map = priorityBySkill([
    priority({ skill: "messaging", impact: 0.9 }),
    priority({ skill: "messaging", impact: 0.1 }),
  ]);
  assert.equal(map.get("messaging")?.impact, 0.9);
});

test("tickets are matched to their OWN skill's priority", () => {
  const phases = packPlanPhases(
    [ticket({ task_key: "conv", skill: "conversion", baseline_score: 5 })],
    [priority({ skill: "messaging", impact: 0.95 }), priority({ skill: "conversion", impact: 0.05 })],
  );
  // Must use conversion's low impact, not messaging's high one.
  assert.deepEqual(phases.map((p) => p.key), ["later"]);
});

// ── content mapping ────────────────────────────────────────────────────────────────

test("a task never renders as a bare title with no guidance", () => {
  const t = ticketToPlanTask(ticket({ action_required: "  ", how_to: null }));
  assert.equal(t.action_required, "Messaging — /");
  assert.equal(t.how_to, "");
  const t2 = ticketToPlanTask(ticket({ how_to: "  ", action_required: "Do the thing" }));
  assert.equal(t2.how_to, "Do the thing");
});

test("before/after scores are carried through for the lift display", () => {
  const t = ticketToPlanTask(ticket({ baseline_score: 30, current_score: 80 }));
  assert.equal(t.baseline_score, 30);
  assert.equal(t.current_score, 80);
});

// ── progress ───────────────────────────────────────────────────────────────────────

test("progress counts only VERIFIED work as done", () => {
  const phases = packPlanPhases([
    ticket({ task_key: "a", status: "verified_completed", baseline_score: 80 }),
    ticket({ task_key: "b", status: "closed_pending_verify", baseline_score: 80 }),
    ticket({ task_key: "c", status: "pending", baseline_score: 80 }),
    ticket({ task_key: "d", status: "in_progress", baseline_score: 80 }),
  ]);
  const p = packPlanProgress(phases);
  assert.equal(p.total, 4);
  assert.equal(p.verified, 1);
  assert.equal(p.in_progress, 2, "closed_pending_verify counts as in progress, not done");
  assert.equal(p.pct, 25);
});

test("progress on an empty pack is 0, not NaN", () => {
  assert.deepEqual(packPlanProgress([]), { total: 0, verified: 0, in_progress: 0, pct: 0 });
});

// ── the Pages ↔ Your plan cross-link (item 3.5) ───────────────────────────────────

test("the cross-link anchor is derived identically from both surfaces", () => {
  // Pages holds SkillPriority.skill + page.url; the plan holds Ticket.skill + page_url.
  // Both are the same verbatim strings (generate_tickets_from_run takes page_url straight
  // from detail_for_pack, which is what GET /api/packs/{run}/{pack} returns as page.url),
  // so one function called from both sides cannot disagree.
  assert.equal(
    packFixDomId("messaging", "https://x.com/pricing"),
    packFixDomId("messaging", "https://x.com/pricing"),
  );
  assert.equal(packFixDomId("messaging", "https://x.com/"), "packfix:messaging@https://x.com/");
});

test("different skills or pages get different anchors", () => {
  assert.notEqual(
    packFixDomId("messaging", "https://x.com/"),
    packFixDomId("conversion", "https://x.com/"),
  );
  assert.notEqual(
    packFixDomId("messaging", "https://x.com/"),
    packFixDomId("messaging", "https://x.com/pricing"),
  );
});

test("a missing skill or page still yields a stable, non-colliding anchor", () => {
  // Never produce `undefined` in a DOM id, and never let two unknowns collide with a real one.
  assert.equal(packFixDomId(null, null), "packfix:?@?");
  assert.equal(packFixDomId(undefined, "https://x.com/"), "packfix:?@https://x.com/");
});
