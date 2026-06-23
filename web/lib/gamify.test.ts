// web/lib/gamify.test.ts
// Run: node --test lib/gamify.test.ts (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import { MATURITY_LABEL, MATURITY_ORDER, maturityProgress } from "./gamify.ts";

test("maturityProgress is 0 at foundations and 1 at cited_leader", () => {
  assert.equal(maturityProgress("foundations"), 0);
  assert.equal(maturityProgress("cited_leader"), 1);
});

test("recommended sits halfway up the ladder", () => {
  assert.equal(maturityProgress("recommended"), MATURITY_ORDER.indexOf("recommended") / (MATURITY_ORDER.length - 1));
});

test("every stage has a human label", () => {
  for (const s of MATURITY_ORDER) assert.ok(MATURITY_LABEL[s]);
});
