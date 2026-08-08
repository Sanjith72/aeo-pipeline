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
  groupFixesByPage,
  pageFixCounts,
  PACK_PHASE_ORDER,
  bucketTicket,
  packFixDomId,
  packFixesDomId,
  packPlanPhases,
  packPlanProgress,
  priorityBySkill,
  ticketsByPack,
  ticketsByPhase,
  ticketStatusToMilestoneStatus,
  ticketToPlanTask,
} from "./packPlan.ts";
import { PHASE_ORDER } from "./phases.ts";
import type { PackPageDetail, SkillPriority, Ticket, TicketStatus } from "./types.ts";

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

// ── grouping fixes by page for the Pages tab ────────────────────────────────────────
//
// Each of these pins a way the obvious implementation (filter tickets by
// `t.page_url === page.url`, inside the existing `detail != null && priorities.length > 0`
// accordion) silently loses a customer's work. They are product states, not hypotheticals —
// see the block comment in packPlan.ts for where each one comes from in the backend.

function tk(pageUrl: string | null, status: TicketStatus, skill = "messaging"): Ticket {
  return {
    id: 1, task_key: `skill:${skill}@${pageUrl}`, label: "l", action_required: "a", how_to: null,
    status, status_source: "manual", detected_at: null, pack_index: 1, assignee: null,
    target_date: null, page_url: pageUrl, skill: skill as Ticket["skill"],
    baseline_score: null, current_score: null, closed_at: null,
  };
}
const pg = (url: string, scored: boolean): PackPageDetail => ({
  url, page_type: "generic", overall: scored ? 50 : null,
  detail: scored ? { skills: {} as never, priorities: [] } : null,
});

test("a page scored this run keeps the server's impact ordering", () => {
  const { groups } = groupFixesByPage([pg("https://x.com/a", true), pg("https://x.com/b", true)], []);
  assert.deepEqual(groups.map((g) => g.url), ["https://x.com/a", "https://x.com/b"]);
});

test("a page with NO scored detail still shows its fixes", () => {
  // Tickets are per-client, not per-run: generate_tickets_from_run preserves open tickets for
  // pages this run did not re-score. Gating the fix list on `detail != null` hides them.
  const { groups } = groupFixesByPage([pg("https://x.com/a", false)], [tk("https://x.com/a", "pending")]);
  assert.equal(groups[0].tickets.length, 1, "an unscored page must not lose its fix list");
});

test("a page with zero priorities still shows its fixes", () => {
  // _skills_with_findings falls back to 'any skill with suggestions', so tickets exist where
  // priorities is []. The old accordion gate `priorities.length > 0` hid the whole list.
  const page = pg("https://x.com/a", true); // detail present, priorities []
  const { groups } = groupFixesByPage([page], [tk("https://x.com/a", "pending")]);
  assert.equal(groups[0].detail?.detail?.priorities.length, 0);
  assert.equal(groups[0].tickets.length, 1);
});

test("a verified ticket whose page left the pack is still shown", () => {
  // The prune NEVER deletes closed_pending_verify / verified_completed tickets — they hold the
  // pinned baseline->current record and the pack's completion signal. A page-keyed list that
  // dropped them would erase the CH-15 delta and shrink the pack's apparent progress.
  const { groups } = groupFixesByPage(
    [pg("https://x.com/a", true)],
    [tk("https://x.com/a", "pending"), tk("https://x.com/gone", "verified_completed")],
  );
  assert.equal(groups.length, 2);
  assert.equal(groups[1].url, "https://x.com/gone");
  assert.equal(groups[1].detail, null, "it has fixes but no scored detail");
  assert.equal(groups[1].tickets[0].status, "verified_completed");
});

test("url spellings that differ only in host case or a trailing slash still match", () => {
  // page_priorities.url and crawled_pages.url_normalized 'can differ in form', and a ticket's
  // page_url is refreshed only when its page is re-scored. Exact equality was fine as a scroll
  // anchor; as an existence test it drops fixes.
  const { groups } = groupFixesByPage(
    [pg("https://x.com/", true)],
    [tk("https://X.com", "pending"), tk("https://x.com/", "pending")],
  );
  assert.equal(groups.length, 1, "must not split one page into two");
  assert.equal(groups[0].tickets.length, 2);
});

test("matching is timid: pages that really differ are never merged", () => {
  // Over-merging hides work, which is the failure this module exists to prevent. Query and
  // path case are left alone on purpose.
  const { groups } = groupFixesByPage(null, [
    tk("https://x.com/a?v=1", "pending"),
    tk("https://x.com/a?v=2", "pending"),
    tk("https://x.com/A", "pending"),
    tk("https://x.com/a", "pending"),
  ]);
  assert.equal(groups.length, 4, "distinct pages must stay distinct");
});

test("a site-wide fix with no page is returned, never dropped or misattached", () => {
  const { groups, sitewide } = groupFixesByPage([pg("https://x.com/a", true)], [tk(null, "pending")]);
  assert.equal(sitewide.length, 1);
  assert.equal(groups[0].tickets.length, 0, "it must not be attached to an arbitrary page");
});

test("no pages and no tickets is empty, not a crash", () => {
  assert.deepEqual(groupFixesByPage(null, null), { groups: [], sitewide: [] });
  assert.deepEqual(groupFixesByPage([], []), { groups: [], sitewide: [] });
});

test("the sidebar badge counts what is still to do, separately from what is proven", () => {
  const counts = pageFixCounts([
    tk("u", "pending"), tk("u", "in_progress"),
    tk("u", "closed_pending_verify"), tk("u", "verified_completed"),
  ]);
  assert.deepEqual(counts, { total: 4, open: 2, verifying: 1, verified: 1 });
});

// ── ticketsByPhase (the plan-card variant) ─────────────────────────────────────────

test("ticketsByPhase buckets identically to packPlanPhases", () => {
  // Two surfaces, one bucketing rule: a fix must sit in the same phase whether "Your plan"
  // renders it as a ticket row or anything else derives the mapped task shape.
  const tickets = [
    ticket({ task_key: "a", baseline_score: 80 }),
    ticket({ task_key: "b", baseline_score: 45 }),
    ticket({ task_key: "c", baseline_score: 10 }),
    ticket({ task_key: "d", baseline_score: null }),
  ];
  const viaTasks = packPlanPhases(tickets);
  const viaTickets = ticketsByPhase(tickets);
  assert.deepEqual(
    viaTickets.map((p) => ({ key: p.key, keys: p.tickets.map((t) => t.task_key).sort() })),
    viaTasks.map((p) => ({ key: p.key, keys: p.tasks.map((t) => t.task_key).sort() })),
  );
});

test("ticketsByPhase keeps the REAL Ticket objects", () => {
  // The row component drives /api/tickets/* with the ticket itself — a flattened copy
  // would strand the caller re-joining on task_key.
  const t = ticket({ task_key: "x", status: "closed_pending_verify" as TicketStatus });
  const phases = ticketsByPhase([t]);
  assert.equal(phases.length, 1);
  assert.equal(phases[0].tickets[0], t);
});

const scoredPage = (url: string, priorities: SkillPriority[]): PackPageDetail => ({
  url,
  page_type: "generic",
  overall: 50,
  detail: { skills: {} as never, priorities },
});

test("ticketsByPhase matches a priority by skill AND page — impact never crosses pages", () => {
  // Impact is a per-page×skill number (CH-06). The homepage's 0.9 messaging impact must
  // phase the HOMEPAGE's ticket; the /pricing ticket (own impact 0.15, score 10) is
  // structural work and must not parade as a Quick Win just because another page listed
  // the same skill first in the flattened pack.
  const home = "https://x.com/";
  const pricing = "https://x.com/pricing";
  const pages = [
    scoredPage(home, [priority({ skill: "messaging", impact: 0.9 })]),
    scoredPage(pricing, [priority({ skill: "messaging", impact: 0.15 })]),
  ];
  const tickets = [
    ticket({ task_key: "home-fix", skill: "messaging", page_url: home, baseline_score: 10 }),
    ticket({ task_key: "pricing-fix", skill: "messaging", page_url: pricing, baseline_score: 10 }),
  ];
  const phases = ticketsByPhase(tickets, pages);
  const phaseOf = (key: string) =>
    phases.find((p) => p.tickets.some((t) => t.task_key === key))?.key;
  assert.equal(phaseOf("home-fix"), "week_1"); // its own page's 0.9
  assert.equal(phaseOf("pricing-fix"), "later"); // its own page's 0.15 — not the homepage's
});

test("ticketsByPhase falls back to the ticket's own score when its page has no priority", () => {
  const t = ticket({
    task_key: "x",
    skill: "messaging",
    page_url: "https://x.com/other",
    baseline_score: 80,
  });
  const phases = ticketsByPhase(
    [t],
    [scoredPage("https://x.com/", [priority({ skill: "messaging", impact: 0.1 })])],
  );
  assert.deepEqual(phases.map((p) => p.key), ["week_1"]); // score 80 → nearly there
});

test("ticketsByPhase sorts unfinished above verified, verifying beside in_progress", () => {
  const tickets = [
    ticket({ task_key: "done", status: "verified_completed" as TicketStatus, baseline_score: 80 }),
    ticket({ task_key: "verifying", status: "closed_pending_verify" as TicketStatus, baseline_score: 80 }),
    ticket({ task_key: "open", status: "pending" as TicketStatus, baseline_score: 80 }),
  ];
  const phases = ticketsByPhase(tickets);
  const order = phases[0].tickets.map((t) => t.task_key);
  assert.equal(order[0], "open"); // to-do first
  assert.equal(order[order.length - 1], "done"); // verified sinks to the bottom
});

test("ticketsByPhase on nothing is an empty plan, not a crash", () => {
  assert.deepEqual(ticketsByPhase(null), []);
  assert.deepEqual(ticketsByPhase([]), []);
  assert.deepEqual(ticketsByPhase(undefined, null), []);
});

// ── grouping the run-wide list under each pack (Your plan's by-pack section) ────────

test("ticketsByPack groups by pack, packs ascending, ticket order preserved within", () => {
  // The run-wide route returns one flat list; "Your plan" renders it under each pack. The
  // within-pack order is the server's and must survive the grouping — reordering here
  // would fight the phase sort applied downstream.
  const grouped = ticketsByPack([
    ticket({ task_key: "p3-a", pack_index: 3 }),
    ticket({ task_key: "p1-a", pack_index: 1 }),
    ticket({ task_key: "p3-b", pack_index: 3 }),
    ticket({ task_key: "p1-b", pack_index: 1 }),
  ]);
  assert.deepEqual([...grouped.keys()], [1, 3]);
  assert.deepEqual(grouped.get(1)?.map((t) => t.task_key), ["p1-a", "p1-b"]);
  assert.deepEqual(grouped.get(3)?.map((t) => t.task_key), ["p3-a", "p3-b"]);
});

test("ticketsByPack on nothing is an empty map, not a crash", () => {
  assert.equal(ticketsByPack(null).size, 0);
  assert.equal(ticketsByPack(undefined).size, 0);
  assert.equal(ticketsByPack([]).size, 0);
});

test("a pack with no tickets is simply absent — the CARD decides how to render that", () => {
  // MilestoneDashboard renders a card for every pack in the grid and asks the map with
  // `?? []`; the map must not invent empty entries that would mask a genuinely missing
  // pack elsewhere.
  const grouped = ticketsByPack([ticket({ pack_index: 2 })]);
  assert.equal(grouped.has(1), false);
  assert.deepEqual(grouped.get(2)?.length, 1);
});

test("the pack grid's jump and the plan's card agree on the anchor id", () => {
  // One function, imported by both sides — this pins the format so a refactor of either
  // caller cannot silently break the scroll-to-pack jump.
  assert.equal(packFixesDomId(2), "pack-fixes-2");
});
