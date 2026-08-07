// Unit tests for the checkout RETURN leg (lib/checkoutReturn.ts). Runs on Node's built-in
// test runner with native TS type-stripping:
//
//   node --test lib/checkoutReturn.test.ts        (or: npm test, from web/)
//
// The bug these pin down: success_path is "/studio?checkout=success" and nothing ever read
// it, so a buyer who paid landed on the studio's normal empty state — no confirmation, no
// run, no pack. A successful payment looked exactly like a failed one.

import test from "node:test";
import assert from "node:assert/strict";

import {
  POLL_DELAYS_MS,
  POLL_TOTAL_MS,
  clearPendingCheckout,
  isPackUnlocked,
  readCheckoutOutcome,
  readPendingCheckout,
  rememberPendingCheckout,
  unlockedDestination,
  urlWithoutCheckoutParams,
} from "./checkoutReturn.ts";

/** An in-memory stand-in for localStorage — the runner has no DOM. */
function fakeStorage(seed: Record<string, string> = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    _map: map,
  };
}

// ── reading the return URL ─────────────────────────────────────────────────────────

test("a successful return is recognised, with the pack and run carried back", () => {
  const o = readCheckoutOutcome("?checkout=success&pack=3&run_id=42");
  assert.equal(o.kind, "success");
  assert.equal(o.kind === "success" && o.packIndex, 3);
  assert.equal(o.kind === "success" && o.runId, 42);
});

test("success works with no pack/run hints (older sessions, or storage-only)", () => {
  const o = readCheckoutOutcome("?checkout=success");
  assert.equal(o.kind, "success");
  assert.equal(o.kind === "success" && o.packIndex, undefined);
});

test("a cancelled return is its own outcome, both spellings", () => {
  assert.equal(readCheckoutOutcome("?checkout=cancelled").kind, "cancelled");
  assert.equal(readCheckoutOutcome("?checkout=canceled").kind, "cancelled");
});

test("an ordinary visit is 'none' — the flow must not fire on every studio load", () => {
  assert.equal(readCheckoutOutcome("").kind, "none");
  assert.equal(readCheckoutOutcome(null).kind, "none");
  assert.equal(readCheckoutOutcome("?domain=example.com&review=1").kind, "none");
  assert.equal(readCheckoutOutcome("?checkout=whatever").kind, "none");
});

test("junk pack/run values are dropped rather than trusted", () => {
  // These come off the URL, so they are attacker-controllable. They only decide which pack
  // to OPEN and which run to reload — never what is unlocked — but a NaN would still break
  // the poll, and a negative or zero index is meaningless.
  for (const q of ["?checkout=success&pack=abc", "?checkout=success&pack=-1",
                   "?checkout=success&pack=0", "?checkout=success&pack=1.5"]) {
    const o = readCheckoutOutcome(q);
    assert.equal(o.kind === "success" && o.packIndex, undefined, q);
  }
  const o = readCheckoutOutcome("?checkout=success&run_id=abc");
  assert.equal(o.kind === "success" && o.runId, undefined);
});

// ── remembering context across the redirect ────────────────────────────────────────

test("pre-checkout context survives a round trip", () => {
  const s = fakeStorage();
  rememberPendingCheckout({ domain: "example.com", packIndex: 2, runId: 7 }, 1_000_000, s);
  const back = readPendingCheckout(1_000_000, s);
  assert.equal(back?.domain, "example.com");
  assert.equal(back?.packIndex, 2);
  assert.equal(back?.runId, 7);
});

test("a stale pending checkout is ignored", () => {
  // An abandoned checkout from days ago must not hijack an ordinary visit.
  const s = fakeStorage();
  rememberPendingCheckout({ domain: "example.com", packIndex: 2 }, 0, s);
  assert.equal(readPendingCheckout(25 * 60 * 60 * 1000, s), null);
});

test("a corrupt or partial entry reads as absent, never as a crash", () => {
  assert.equal(readPendingCheckout(1, fakeStorage({ "aeo:pending-checkout": "not json" })), null);
  assert.equal(readPendingCheckout(1, fakeStorage({ "aeo:pending-checkout": "{}" })), null);
  assert.equal(
    readPendingCheckout(1, fakeStorage({ "aeo:pending-checkout": '{"domain":"","packIndex":2,"savedAt":1}' })),
    null,
  );
  assert.equal(
    readPendingCheckout(1, fakeStorage({ "aeo:pending-checkout": '{"domain":"x.com","savedAt":1}' })),
    null,
  );
});

test("clearing removes it", () => {
  const s = fakeStorage();
  rememberPendingCheckout({ domain: "example.com", packIndex: 2 }, 1000, s);
  clearPendingCheckout(s);
  assert.equal(readPendingCheckout(1000, s), null);
});

test("a storage that throws never breaks the sale", () => {
  // Safari private mode throws on setItem. Failing to remember is recoverable — the
  // success_url still carries pack + run_id — but throwing here would abort the redirect.
  const hostile = {
    getItem: () => { throw new Error("denied"); },
    setItem: () => { throw new Error("denied"); },
    removeItem: () => { throw new Error("denied"); },
  };
  assert.doesNotThrow(() => rememberPendingCheckout({ domain: "x.com", packIndex: 2 }, 1, hostile));
  assert.equal(readPendingCheckout(1, hostile), null);
  assert.doesNotThrow(() => clearPendingCheckout(hostile));
});

// ── deciding whether the grant landed ──────────────────────────────────────────────

test("a pack counts as unlocked only when the server says locked:false", () => {
  const packs = [{ pack_index: 1, locked: false }, { pack_index: 2, locked: true }];
  assert.equal(isPackUnlocked(packs, 1), true);
  assert.equal(isPackUnlocked(packs, 2), false);
});

test("an absent pack is 'not yet', never a spurious success", () => {
  // A run whose packs have not persisted yet must keep polling rather than declare victory.
  assert.equal(isPackUnlocked([{ pack_index: 1, locked: false }], 3), false);
  assert.equal(isPackUnlocked([], 1), false);
  assert.equal(isPackUnlocked(null, 1), false);
  assert.equal(isPackUnlocked(undefined, 1), false);
});

test("a pack with no locked flag is not treated as unlocked", () => {
  assert.equal(isPackUnlocked([{ pack_index: 1 }], 1), false);
});

// ── the poll schedule ──────────────────────────────────────────────────────────────

test("the poll waits about 20s in total and starts fast", () => {
  // The webhook usually lands in a second or two, so the early checks are quick (the common
  // case must feel instant); the later gaps stretch so a slow webhook doesn't turn into
  // twenty hammering requests.
  assert.ok(POLL_TOTAL_MS >= 18_000 && POLL_TOTAL_MS <= 25_000, `total was ${POLL_TOTAL_MS}ms`);
  assert.ok(POLL_DELAYS_MS[0] <= 500);
  for (let i = 1; i < POLL_DELAYS_MS.length; i++) {
    assert.ok(POLL_DELAYS_MS[i] >= POLL_DELAYS_MS[i - 1], "delays must be non-decreasing");
  }
});

// ── cleaning the URL ───────────────────────────────────────────────────────────────

test("checkout params are stripped so a refresh cannot re-run the flow", () => {
  assert.equal(
    urlWithoutCheckoutParams("/studio", "?checkout=success&pack=2&run_id=9"),
    "/studio",
  );
});

test("stripping preserves every unrelated param and the hash", () => {
  assert.equal(
    urlWithoutCheckoutParams("/studio", "?checkout=success&pack=2&domain=example.com", "#plan"),
    "/studio?domain=example.com#plan",
  );
});

test("stripping is a no-op on an ordinary URL", () => {
  assert.equal(urlWithoutCheckoutParams("/studio", "?domain=example.com"), "/studio?domain=example.com");
  assert.equal(urlWithoutCheckoutParams("/studio", ""), "/studio");
});

// ── where the acknowledgement points (the "it's open below" lie) ────────────────────
//
// The notice claimed the pack was "open below" while living inside StudioApp's
// `view === "results"` branch, which a Stripe return never reaches — so it rendered nowhere
// at all. Moved out, it can be on screen in the wizard too, where "below" is false.

test("with the pack grid on screen, below is the honest answer", () => {
  assert.deepEqual(unlockedDestination({ packsVisible: true }), { kind: "below" });
  // Even with a plan id available, on-screen beats a navigation.
  assert.deepEqual(unlockedDestination({ packsVisible: true, planId: "abc" }), { kind: "below" });
});

test("off the results view, a saved plan is where the pack actually lives", () => {
  assert.deepEqual(unlockedDestination({ packsVisible: false, planId: "abc123" }), {
    kind: "plan",
    href: "/plan/abc123",
  });
});

test("with nowhere to point, say unknown rather than invent a destination", () => {
  assert.deepEqual(unlockedDestination({ packsVisible: false }), { kind: "unknown" });
  assert.deepEqual(unlockedDestination({ packsVisible: false, planId: null }), { kind: "unknown" });
  assert.deepEqual(unlockedDestination({ packsVisible: false, planId: "" }), { kind: "unknown" });
  // Whitespace is not an id — a "/plan/ " link would 404 on a paying customer.
  assert.deepEqual(unlockedDestination({ packsVisible: false, planId: "   " }), { kind: "unknown" });
});
