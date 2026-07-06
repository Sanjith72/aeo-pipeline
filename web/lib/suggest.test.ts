// Pins the client half of the /api/competitors/suggest reason contract (the server
// half lives in tests/unit/test_api.py). types.ts widens `reason` with `| string`,
// so tsc can NOT flag a typo'd literal — only this test can. Runs on Node's built-in
// test runner with native TS type-stripping:
//
//   node --test lib/suggest.test.ts        (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import { classifySuggestResponse, unavailableCopy } from "./suggest.ts";

const COMP = [{ name: "Rapid7", domain: "rapid7.com" }];

test("non-empty llm/onsite responses are ready", () => {
  assert.deepEqual(classifySuggestResponse({ competitors: COMP, source: "llm" }), { kind: "ready" });
  assert.deepEqual(classifySuggestResponse({ competitors: COMP, source: "onsite" }), { kind: "ready" });
});

test("llm_failed maps to the retry-worthy error state, not the honest blank", () => {
  assert.deepEqual(
    classifySuggestResponse({ competitors: [], source: "unavailable", reason: "llm_failed" }),
    { kind: "error" },
  );
});

test("llm_disabled / no_results / verification_failed stay unavailable, reason attached", () => {
  for (const reason of ["llm_disabled", "no_results", "verification_failed"]) {
    assert.deepEqual(
      classifySuggestResponse({ competitors: [], source: "unavailable", reason }),
      { kind: "unavailable", reason },
    );
  }
});

test("an older backend without a reason field still lands in unavailable", () => {
  assert.deepEqual(classifySuggestResponse({ competitors: [], source: "unavailable" }), {
    kind: "unavailable",
    reason: undefined,
  });
});

test("an empty list under a non-unavailable source is treated as unavailable, not ready", () => {
  // Defensive: the backend never sends source=llm with zero competitors today, but a
  // blank must never render as a zero-item "ready" grid.
  assert.equal(classifySuggestResponse({ competitors: [], source: "llm" }).kind, "unavailable");
});

test("unavailable copy is honest about a server with AI switched off", () => {
  assert.match(unavailableCopy("llm_disabled"), /aren't enabled on this server/);
  assert.match(unavailableCopy("no_results"), /couldn't find recommendations/);
  assert.match(unavailableCopy(undefined), /couldn't find recommendations/);
});
