// Pure mapping from a /api/competitors/suggest response to the CompetitorPicker's next
// UI state. Extracted from the component so the reason-string contract with the backend
// is pinned by a unit test on THIS side of the JSON boundary too (the server side is
// pinned in tests/unit/test_api.py) — the `| string` widening in types.ts means tsc
// cannot catch a drifted or typo'd reason literal here.
//
// The mapping in one line: anything usable → ready; llm_failed (providers errored —
// transient) → the amber retry state; every other blank (llm_disabled, no_results,
// verification_failed, unknown/absent reason from an older backend) → the neutral
// "unavailable" state, carrying the reason so the copy can be honest about why.

import type { CompetitorSuggestResponse } from "./types";

export type SuggestClassification =
  | { kind: "ready" }
  | { kind: "error" }
  | { kind: "unavailable"; reason?: string };

export function classifySuggestResponse(res: CompetitorSuggestResponse): SuggestClassification {
  if (res.source !== "unavailable" && res.competitors.length > 0) return { kind: "ready" };
  if (res.reason === "llm_failed") return { kind: "error" };
  return { kind: "unavailable", reason: res.reason };
}

/** The neutral-state copy, honest about WHY the list is empty. */
export function unavailableCopy(reason?: string): string {
  return reason === "llm_disabled"
    ? "AI recommendations aren't enabled on this server yet — add the competitors you know below, or skip this step."
    : "We couldn't find recommendations for this business yet — add the competitors you know below, or skip this step entirely.";
}
