// Unit tests for the predicted-lift display helpers (Feature #2). Runs on Node's built-in
// test runner with native TS type-stripping — no extra dependency:
//
//   node --test lib/predictedLift.test.ts        (or: npm test, from web/)
//
// The cardinal rule under test: only a real, positive, simulated estimate shows "+X pts";
// every other state shows "—", never a fabricated "+0".

import test from "node:test";
import assert from "node:assert/strict";

import { predictedLiftChip, reconcileLabel } from "./predictedLift.ts";
import type { PredictedLift } from "./types.ts";

function lift(over: Partial<PredictedLift>): PredictedLift {
  return { point: null, low: null, high: null, unit: "rubric_points", basis: "unknown", ...over };
}

test("a real simulated lift renders +X pts with a tier-short band", () => {
  const chip = predictedLiftChip(lift({ point: 3, low: 2, high: 3, basis: "simulated" }));
  assert.equal(chip.known, true);
  assert.equal(chip.label, "+3 pts");
  assert.equal(chip.band, "+2 to +3");
});

test("a simulated lift with no band gap omits the band", () => {
  const chip = predictedLiftChip(lift({ point: 2, low: 2, high: 2, basis: "simulated" }));
  assert.equal(chip.label, "+2 pts");
  assert.equal(chip.band, null);
});

test("an unknown (advisory) estimate renders — never a number", () => {
  const chip = predictedLiftChip(lift({ basis: "unknown" }));
  assert.equal(chip.known, false);
  assert.equal(chip.label, "—");
});

test("no_deterministic_lift renders — never +0", () => {
  const chip = predictedLiftChip(lift({ point: 0, low: 0, high: 0, basis: "no_deterministic_lift" }));
  assert.equal(chip.known, false);
  assert.equal(chip.label, "—");
});

test("a null prediction (or missing) is safe", () => {
  assert.equal(predictedLiftChip(null).label, "—");
  assert.equal(predictedLiftChip(undefined).label, "—");
});

test("a simulated point of 0 is still — (never fabricated)", () => {
  // Defensive: even mislabelled, a non-positive point can't show as a positive chip.
  assert.equal(predictedLiftChip(lift({ point: 0, basis: "simulated" })).label, "—");
});

test("reconcile shows predicted vs actual when both known", () => {
  assert.equal(reconcileLabel({ predicted_delta: 2, actual_delta: 3 }), "predicted +2 · actual +3");
});

test("reconcile shows whichever side is known", () => {
  assert.equal(reconcileLabel({ predicted_delta: 2, actual_delta: null }), "predicted +2");
  assert.equal(reconcileLabel({ predicted_delta: null, actual_delta: 3 }), "actual +3");
});

test("reconcile is null when neither side is known", () => {
  assert.equal(reconcileLabel({ predicted_delta: null, actual_delta: null }), null);
  assert.equal(reconcileLabel({}), null);
});
