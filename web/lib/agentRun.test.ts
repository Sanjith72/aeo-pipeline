// web/lib/agentRun.test.ts
// Unit tests for summarizeRun. Run: node --test lib/agentRun.test.ts (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import { summarizeRun } from "./agentRun.ts";
import type { AgentRunDetail } from "./types.ts";

function run(over: Partial<AgentRunDetail>): AgentRunDetail {
  return {
    id: "r1",
    status: "staged",
    result: {
      tasks: [
        { id: "page:/a", title: "A", status: "reviewed", draft: { word_count: 100 }, critic: { passed: true, independent_passed: true, claims_flagged: false, claims: [], needs_review: false } },
        { id: "page:/b", title: "B", status: "flagged", draft: { word_count: 80 }, critic: { passed: false, independent_passed: false, claims_flagged: true, claims: ["#1"], needs_review: true } },
      ],
    },
    steps: [
      { seq: 1, agent: "planner", status: "ok" },
      { seq: 2, agent: "builder", status: "ok", cost_usd: 0.01 },
      { seq: 3, agent: "critic", status: "ok", cost_usd: 0.02 },
    ],
    ...over,
  };
}

test("counts drafted, flagged, and totals cost", () => {
  const s = summarizeRun(run({}));
  assert.equal(s.taskCount, 2);
  assert.equal(s.draftedCount, 2);
  assert.equal(s.flaggedCount, 1);
  assert.equal(s.costUsd.toFixed(2), "0.03");
});

test("isStaged reflects status", () => {
  assert.equal(summarizeRun(run({ status: "staged" })).isStaged, true);
  assert.equal(summarizeRun(run({ status: "approved" })).isStaged, false);
});

test("empty/absent result is safe", () => {
  const s = summarizeRun({ id: "x", status: "queued" });
  assert.equal(s.taskCount, 0);
  assert.equal(s.flaggedCount, 0);
  assert.equal(s.costUsd, 0);
});
