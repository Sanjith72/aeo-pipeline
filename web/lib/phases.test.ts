// Unit tests for the Roadmap↔Strategy merge (lib/phases.ts). Runs on Node's built-in
// test runner with native TS type-stripping:
//
//   node --test lib/phases.test.ts        (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import {
  dedupeActionsAgainstPlan,
  groupActionsByPhase,
  phaseDisplayTitle,
  phaseForAction,
  strategyExtrasState,
} from "./phases.ts";
import type { PlanTask, StrategyAction, StructuredPlan } from "./types.ts";

function action(over: Partial<StrategyAction>): StrategyAction {
  return {
    priority: 1,
    title: "Do a thing",
    detail: "",
    category: "content",
    effort: "medium",
    related_slugs: [],
    ...over,
  };
}

function task(over: Partial<PlanTask>): PlanTask {
  return {
    id: "page:/x",
    label: "A task",
    phase: "week_1",
    quick_win: false,
    effort: "low",
    impact_score: 0.5,
    current_state: "",
    action_required: "",
    how_to: "",
    ...over,
  };
}

function plan(tasks: PlanTask[]): StructuredPlan {
  return {
    phases: [{ key: "week_1", title: "Quick Wins", blurb: "", tasks }],
    quick_win_ids: [],
    quick_win_count: 0,
    total: tasks.length,
  };
}

test("drops an action whose related slug the plan tracks as a page task", () => {
  const actions = [action({ title: "Create an FAQ resource", related_slugs: ["/faq"] })];
  const p = plan([task({ id: "page:/faq", label: "Create your FAQ page" })]);
  assert.deepEqual(dedupeActionsAgainstPlan(actions, p), []);
});

test("drops an action whose title strongly overlaps a task label", () => {
  const actions = [action({ title: "Add customer reviews and testimonials" })];
  const p = plan([
    task({ id: "vis:reviews", label: "Ask three happy customers for a Google review", action_required: "Collect customer reviews and testimonials for your site" }),
  ]);
  assert.deepEqual(dedupeActionsAgainstPlan(actions, p), []);
});

test("keeps an unrelated action and sorts survivors by priority", () => {
  const actions = [
    action({ priority: 3, title: "Launch a comparison hub for your niche" }),
    action({ priority: 1, title: "Sponsor local industry events" }),
  ];
  const p = plan([task({ id: "page:/about", label: "Improve your About page" })]);
  const out = dedupeActionsAgainstPlan(actions, p);
  assert.deepEqual(out.map((a) => a.priority), [1, 3]);
});

test("with no plan, every action survives (sorted)", () => {
  const actions = [action({ priority: 2 }), action({ priority: 1, title: "Another move" })];
  const out = dedupeActionsAgainstPlan(actions, null);
  assert.equal(out.length, 2);
  assert.deepEqual(out.map((a) => a.priority), [1, 2]);
});

test("effort maps to the roadmap phase order", () => {
  assert.equal(phaseForAction(action({ effort: "low" })), "week_1");
  assert.equal(phaseForAction(action({ effort: "medium" })), "week_2_4");
  assert.equal(phaseForAction(action({ effort: "high" })), "later");
});

test("groupActionsByPhase orders Quick Wins → Foundation → Growth & Scale and drops empties", () => {
  const groups = groupActionsByPhase([
    action({ effort: "high", title: "big" }),
    action({ effort: "low", title: "small" }),
  ]);
  assert.deepEqual(groups.map((g) => g.key), ["week_1", "later"]);
  assert.equal(phaseDisplayTitle(groups[0].key, "x"), "Quick Wins");
  assert.equal(phaseDisplayTitle(groups[1].key, "x"), "Growth & Scale");
});

// ── strategyExtrasState (Phase 3 item 3.3) ────────────────────────────────────────
// "Bigger strategic moves" now renders in the Overview tab, which can paint long before
// the plan is built. dedupeActionsAgainstPlan returns every action UNFILTERED when there
// are no tasks to compare against — correct for its own contract, wrong as a render input,
// because the user would see the raw audit list and then watch entries silently vanish as
// the plan arrives and the dedupe starts biting.

test("pending while there is no plan to dedupe against", () => {
  const actions = [action({ title: "Add a pricing page" })];
  assert.equal(strategyExtrasState(actions, null).kind, "pending");
  assert.equal(strategyExtrasState(actions, undefined).kind, "pending");
});

test("a plan with zero tasks is also pending, not an unfiltered list", () => {
  // Indistinguishable from "no plan" for dedupe purposes — the filter is a no-op either
  // way, so showing the list would be just as misleading.
  assert.equal(strategyExtrasState([action({})], plan([])).kind, "pending");
});

test("ready once the plan exists, carrying the DEDUPED actions", () => {
  const actions = [
    action({ title: "Create an FAQ resource", related_slugs: ["/faq"] }),
    action({ title: "Publish an original research report", priority: 2 }),
  ];
  const state = strategyExtrasState(actions, plan([task({ id: "page:/faq" })]));
  assert.equal(state.kind, "ready");
  assert.deepEqual(
    state.kind === "ready" ? state.actions.map((a) => a.title) : [],
    ["Publish an original research report"],
    "the action the plan already tracks must not appear in Overview as well",
  );
});

test("empty when there are no actions at all, regardless of plan", () => {
  assert.equal(strategyExtrasState([], null).kind, "empty");
  assert.equal(strategyExtrasState([], plan([task({})])).kind, "empty");
  assert.equal(strategyExtrasState(null, null).kind, "empty");
  assert.equal(strategyExtrasState(undefined, undefined).kind, "empty");
});

test("empty when the plan already covers everything", () => {
  // Nothing survives the dedupe -> render nothing, rather than an empty titled card.
  const actions = [action({ title: "Create an FAQ resource", related_slugs: ["/faq"] })];
  assert.equal(strategyExtrasState(actions, plan([task({ id: "page:/faq" })])).kind, "empty");
});
