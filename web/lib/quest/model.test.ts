// web/lib/quest/model.test.ts
// Run: node --test "lib/**/*.test.ts" (or npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import type {
  Milestone,
  MilestoneDashboard,
  MilestoneStatus,
  MilestoneTask,
  PlanTask,
  StructuredPlan,
} from "../types.ts";
import {
  assignEnemy,
  banked,
  buildQuestModel,
  chestBonusFor,
  coinsForImpact,
  enemySize,
} from "./model.ts";
import { themeForPhase } from "./theme.ts";

// ── fixtures ────────────────────────────────────────────────────────────────
const PIRATE = themeForPhase("week_1", 0).roster;

function mtask(
  task_key: string,
  status: MilestoneStatus = "pending",
  status_source: "manual" | "crawl" = "manual",
  position = 0,
): MilestoneTask {
  return {
    id: position + 1,
    task_key,
    label: `Do ${task_key}`,
    action_required: `Action for ${task_key}`,
    how_to: "how",
    verify_kind: task_key.startsWith("vis:") ? "manual" : "page",
    verify_target: null,
    status,
    status_source,
    detected_at: null,
    position,
  } as MilestoneTask;
}

function milestone(milestone_key: string, tasks: MilestoneTask[], position: number): Milestone {
  const status: MilestoneStatus = tasks.every((t) => t.status === "verified_completed")
    ? "verified_completed"
    : "pending";
  return { milestone_key, title: `Phase ${position + 1}`, blurb: "", status, position, tasks };
}

function dash(milestones: Milestone[]): MilestoneDashboard {
  const all = milestones.flatMap((m) => m.tasks);
  const verified = all.filter((t) => t.status === "verified_completed").length;
  return {
    milestones,
    progress: {
      total: all.length,
      verified,
      in_progress: all.filter((t) => t.status === "in_progress").length,
      pct: all.length ? Math.round((verified / all.length) * 100) : 0,
    },
  };
}

function ptask(id: string, impact: number, phase: PlanTask["phase"]): PlanTask {
  return {
    id,
    label: `Do ${id}`,
    phase,
    quick_win: false,
    effort: "medium",
    impact_score: impact,
    current_state: "x",
    action_required: "y",
    how_to: "z",
  } as PlanTask;
}

function plan(tasks: PlanTask[]): StructuredPlan {
  const phases = (["week_1", "week_2_4", "later"] as const)
    .map((key) => ({ key, title: key, blurb: "", tasks: tasks.filter((t) => t.phase === key) }))
    .filter((p) => p.tasks.length);
  return { phases, quick_win_ids: [], quick_win_count: 0, total: tasks.length };
}

const enemyName = (slot: number): string => PIRATE[slot].name;

// ── enemy scaling ───────────────────────────────────────────────────────────
test("first task is always the weakest enemy, last is always the named boss", () => {
  for (const count of [1, 2, 3, 6, 9]) {
    const first = assignEnemy(0, count);
    const last = assignEnemy(count - 1, count);
    assert.equal(enemyName(last.slot), PIRATE[PIRATE.length - 1].name, `count=${count} boss`);
    assert.equal(last.isBoss, true);
    if (count > 1) {
      assert.equal(enemyName(first.slot), PIRATE[0].name, `count=${count} weakest`);
      assert.equal(first.isBoss, false);
    }
  }
});

test("a 3-task phase compresses to deckhand / skeleton king / fleet (spec example)", () => {
  assert.equal(enemyName(assignEnemy(0, 3).slot), "Pirate deckhand");
  assert.equal(enemyName(assignEnemy(1, 3).slot), "Skeleton king");
  assert.equal(enemyName(assignEnemy(2, 3).slot), "Rival pirate fleet");
});

test("with more tasks than enemies, only the last is the boss", () => {
  const count = 9;
  const bossCount = Array.from({ length: count }, (_, i) => assignEnemy(i, count)).filter(
    (e) => e.isBoss,
  ).length;
  assert.equal(bossCount, 1);
});

test("coins scale with impact; enemy size grows with impact and bosses are bigger", () => {
  assert.ok(coinsForImpact(0.9) > coinsForImpact(0.2));
  assert.ok(enemySize(0.9, false) > enemySize(0.2, false));
  assert.ok(enemySize(0.5, true) > enemySize(0.5, false));
});

test("chest bonus always beats any single task's coins", () => {
  const coins = [80, 200, 120];
  assert.ok(chestBonusFor(coins) > Math.max(...coins));
});

// ── model projection ──────────────────────────────────────────────────────────
test("node states: completed / active (first incomplete) / locked (rest)", () => {
  const d = dash([
    milestone(
      "week_1",
      [
        mtask("page:/a", "verified_completed", "manual", 0),
        mtask("page:/b", "pending", "manual", 1),
        mtask("page:/c", "pending", "manual", 2),
      ],
      0,
    ),
  ]);
  const p = plan([ptask("page:/a", 0.9, "week_1"), ptask("page:/b", 0.5, "week_1"), ptask("page:/c", 0.3, "week_1")]);
  const ph = buildQuestModel(p, d).phases[0];
  assert.deepEqual(
    ph.tasks.map((t) => t.nodeState),
    ["completed", "active", "locked"],
  );
  assert.equal(ph.pct, 33);
});

test("out-of-order completion still shows a defeated enemy mid-trail", () => {
  const d = dash([
    milestone(
      "week_1",
      [
        mtask("page:/a", "pending", "manual", 0),
        mtask("page:/b", "verified_completed", "manual", 1),
        mtask("page:/c", "pending", "manual", 2),
      ],
      0,
    ),
  ]);
  const ph = buildQuestModel(plan([]), d).phases[0];
  assert.deepEqual(
    ph.tasks.map((t) => t.nodeState),
    ["active", "completed", "locked"],
  );
});

test("verifiedLive only when completed by the crawl, not by manual attestation", () => {
  const d = dash([
    milestone(
      "week_1",
      [
        mtask("page:/a", "verified_completed", "crawl", 0),
        mtask("page:/b", "verified_completed", "manual", 1),
      ],
      0,
    ),
  ]);
  const [a, b] = buildQuestModel(plan([]), d).phases[0].tasks;
  assert.equal(a.verifiedLive, true);
  assert.equal(b.verifiedLive, false);
});

test("phase 2 stays locked until phase 1 is 100%, then unlocks", () => {
  const locked = buildQuestModel(
    plan([]),
    dash([
      milestone("week_1", [mtask("page:/a", "pending", "manual", 0)], 0),
      milestone("week_2_4", [mtask("page:/b", "pending", "manual", 0)], 1),
    ]),
  );
  assert.equal(locked.phases[0].locked, false);
  assert.equal(locked.phases[1].locked, true);
  // no active node inside a locked phase
  assert.ok(locked.phases[1].tasks.every((t) => t.nodeState !== "active"));

  const unlocked = buildQuestModel(
    plan([]),
    dash([
      milestone("week_1", [mtask("page:/a", "verified_completed", "manual", 0)], 0),
      milestone("week_2_4", [mtask("page:/b", "pending", "manual", 0)], 1),
    ]),
  );
  assert.equal(unlocked.phases[1].locked, false);
  assert.equal(unlocked.phases[1].tasks[0].nodeState, "active");
});

test("a zero-task phase is vacuously complete and never deadlocks the lock cascade", () => {
  const m = buildQuestModel(
    plan([]),
    dash([
      milestone("week_1", [], 0), // empty phase (defensive: build_plan drops these today)
      milestone("week_2_4", [mtask("page:/b", "pending", "manual", 0)], 1),
    ]),
  );
  assert.equal(m.phases[0].isComplete, true);
  assert.equal(m.phases[1].locked, false); // not deadlocked behind the empty phase
});

test("earned coins include the chest bonus once a phase is fully banked; globalCoins sums phases", () => {
  const m = buildQuestModel(
    plan([ptask("page:/a", 1, "week_1")]),
    dash([milestone("week_1", [mtask("page:/a", "verified_completed", "crawl", 0)], 0)]),
  );
  const ph = m.phases[0];
  assert.equal(ph.isComplete, true);
  assert.equal(ph.coinsEarned, coinsForImpact(1) + ph.chestBonus);
  assert.equal(m.globalCoins, ph.coinsEarned);
});

// ── coin banking ──────────────────────────────────────────────────────────────
test("banked() predicate: crawl-verified banks, off-site self-report banks, crawlable manual mark does not", () => {
  const mk = (
    status: MilestoneStatus,
    statusSource: "manual" | "crawl" | null,
    verify_kind: MilestoneTask["verify_kind"],
  ) => banked({ status, statusSource, milestoneTask: { verify_kind } });
  assert.equal(mk("verified_completed", "crawl", "page"), true); // verifiedLive
  assert.equal(mk("verified_completed", "manual", "manual"), true); // off-site, self-reported
  assert.equal(mk("verified_completed", "manual", "page"), false); // crawlable, not yet verified
  assert.equal(mk("verified_completed", "manual", "service"), false);
  assert.equal(mk("verified_completed", "manual", "heading"), false);
  assert.equal(mk("in_progress", "crawl", "page"), false); // status gates everything
  assert.equal(mk("pending", "manual", "manual"), false);
});

test("pending tasks still progress the phase, but only banked coins pay out", () => {
  const d = dash([
    milestone(
      "week_1",
      [
        mtask("page:/a", "verified_completed", "crawl", 0), // banked (crawl)
        mtask("vis:gbp", "verified_completed", "manual", 1), // banked (off-site self-report)
        mtask("page:/c", "verified_completed", "manual", 2), // pending — marked, unverified
        mtask("page:/d", "pending", "manual", 3),
      ],
      0,
    ),
  ]);
  const ph = buildQuestModel(plan([]), d).phases[0];
  assert.deepEqual(
    ph.tasks.map((t) => t.coinsBanked),
    [true, true, false, false],
  );
  // Progression is untouched: the marked task is a defeated node and counts toward pct…
  assert.deepEqual(
    ph.tasks.map((t) => t.nodeState),
    ["completed", "completed", "completed", "active"],
  );
  assert.equal(ph.pct, 75);
  // …but its coins wait for the crawl, and the chest stays shut (phase not fully banked).
  assert.equal(ph.coinsEarned, ph.tasks[0].coins + ph.tasks[1].coins);
});

test("chest bonus banks only when every task in the phase is banked, not merely complete", () => {
  const mixed = buildQuestModel(
    plan([]),
    dash([
      milestone(
        "week_1",
        [
          mtask("page:/a", "verified_completed", "crawl", 0),
          mtask("page:/b", "verified_completed", "manual", 1),
        ],
        0,
      ),
    ]),
  ).phases[0];
  assert.equal(mixed.isComplete, true); // progression says done…
  assert.equal(mixed.coinsEarned, mixed.tasks[0].coins); // …but no chest until /b verifies

  const full = buildQuestModel(
    plan([]),
    dash([
      milestone(
        "week_1",
        [
          mtask("page:/a", "verified_completed", "crawl", 0),
          mtask("page:/b", "verified_completed", "crawl", 1),
        ],
        0,
      ),
    ]),
  ).phases[0];
  assert.equal(full.coinsEarned, full.tasks[0].coins + full.tasks[1].coins + full.chestBonus);
});

test("phase unlock stays on mark-complete even while coins are pending verification", () => {
  const m = buildQuestModel(
    plan([]),
    dash([
      milestone("week_1", [mtask("page:/a", "verified_completed", "manual", 0)], 0),
      milestone("week_2_4", [mtask("page:/b", "pending", "manual", 0)], 1),
    ]),
  );
  assert.equal(m.phases[0].isComplete, true);
  assert.equal(m.phases[1].locked, false); // next phase opens on the mark…
  assert.equal(m.globalCoins, 0); // …while the coins wait for "Check my site"
});

test("maxCoins totals every task's coins plus every chest bonus, regardless of status", () => {
  const m = buildQuestModel(
    plan([ptask("page:/a", 1, "week_1"), ptask("vis:gbp", 0.2, "week_2_4")]),
    dash([
      milestone("week_1", [mtask("page:/a", "pending", "manual", 0)], 0),
      milestone("week_2_4", [mtask("vis:gbp", "verified_completed", "manual", 0)], 1),
    ]),
  );
  const expected =
    coinsForImpact(1) + m.phases[0].chestBonus + coinsForImpact(0.2) + m.phases[1].chestBonus;
  assert.equal(m.maxCoins, expected);
  assert.ok(m.globalCoins <= m.maxCoins);
});
