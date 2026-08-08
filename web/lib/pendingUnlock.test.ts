// Unit tests for the remembered unlock intent (lib/pendingUnlock.ts).
//
//   node --test lib/pendingUnlock.test.ts        (or: npm test, from web/)
//
// The bug these pin down: `handleUnlock` did `if (!user) { openAuth("unlock-pack"); return; }`
// and dropped `packIndex` on the floor. A visitor clicked Unlock on a pack, signed in, and
// arrived back at the same grid with no dialog — the click had been forgotten. `authReason`
// only ever selected a line of copy.

import test from "node:test";
import assert from "node:assert/strict";

import {
  clearPendingUnlock,
  readPendingUnlock,
  rememberPendingUnlock,
  signInReturnPath,
  unlockReturnPath,
} from "./pendingUnlock.ts";

/** In-memory stand-in for localStorage — the runner has no DOM. */
function fakeStore(seed: Record<string, string> = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    _map: map,
  };
}

const NOW = 1_800_000_000_000;

// ── round trip ─────────────────────────────────────────────────────────────────────

test("the pack that was clicked survives the sign-in", () => {
  const s = fakeStore();
  rememberPendingUnlock({ domain: "example.com", packIndex: 3, runId: 42 }, NOW, s);
  const got = readPendingUnlock(NOW + 5_000, s);
  assert.equal(got?.packIndex, 3);
  assert.equal(got?.domain, "example.com");
  assert.equal(got?.runId, 42);
});

test("a generic unlock click with no pack is still remembered", () => {
  const s = fakeStore();
  rememberPendingUnlock({ domain: "example.com", packIndex: null }, NOW, s);
  const got = readPendingUnlock(NOW, s);
  assert.notEqual(got, null);
  assert.equal(got?.packIndex, null);
});

test("a resumed plan's intent is tagged so it cannot fire on a different plan", () => {
  const s = fakeStore();
  rememberPendingUnlock({ domain: "example.com", packIndex: 2, planStateId: "abc" }, NOW, s);
  assert.equal(readPendingUnlock(NOW, s)?.planStateId, "abc");
});

test("clearing means it does not fire twice", () => {
  const s = fakeStore();
  rememberPendingUnlock({ domain: "example.com", packIndex: 2 }, NOW, s);
  clearPendingUnlock(s);
  assert.equal(readPendingUnlock(NOW, s), null);
});

// ── the record must never be trusted as data ───────────────────────────────────────

test("a stale intent expires rather than reopening a dialog days later", () => {
  const s = fakeStore();
  rememberPendingUnlock({ domain: "example.com", packIndex: 2 }, NOW, s);
  assert.notEqual(readPendingUnlock(NOW + 59 * 60 * 1000, s), null);
  assert.equal(readPendingUnlock(NOW + 61 * 60 * 1000, s), null);
});

test("corrupt or hand-edited storage reads as no intent, never as a crash", () => {
  for (const raw of ["", "{", "null", "[]", '{"domain":"x"}', '{"savedAt":"soon"}']) {
    assert.equal(readPendingUnlock(NOW, fakeStore({ "aeo:pending-unlock": raw })), null, raw);
  }
});

test("a nonsense pack index degrades to null instead of selecting pack NaN", () => {
  // This value chooses which dialog to reopen. It must never be able to assert an
  // entitlement, and it must not be able to index anything absurd.
  for (const bad of ["0", "-3", "1.5", '"2"', "null"]) {
    const s = fakeStore({
      "aeo:pending-unlock": `{"domain":"x","packIndex":${bad},"savedAt":${NOW}}`,
    });
    assert.equal(readPendingUnlock(NOW, s)?.packIndex, null, bad);
  }
});

test("a missing storage (private mode) is survivable in both directions", () => {
  assert.doesNotThrow(() => rememberPendingUnlock({ domain: "x", packIndex: 1 }, NOW, null));
  assert.equal(readPendingUnlock(NOW, null), null);
  assert.doesNotThrow(() => clearPendingUnlock(null));
});

// ── where sign-in returns to ───────────────────────────────────────────────────────

test("sign-in returns to the page the user actually left", () => {
  // AuthModal hardcoded next=/studio, so a visitor reading /plan/<id> was dumped into the
  // wizard with no run, no packs and no plan — somewhere they never asked to be.
  assert.equal(signInReturnPath("/plan/abc123"), "/plan/abc123");
  assert.equal(signInReturnPath("/studio"), "/studio");
  assert.equal(signInReturnPath("/overview?domain=x.com"), "/overview?domain=x.com");
});

test("the return path cannot be turned into an open redirect", () => {
  for (const bad of ["https://evil.example.com", "//evil.example.com", "evil.example.com", ""]) {
    assert.equal(signInReturnPath(bad), "/studio", bad);
  }
  assert.equal(signInReturnPath(null), "/studio");
});

test("returning to an auth route would bounce the user back into sign-in", () => {
  assert.equal(signInReturnPath("/auth/callback"), "/studio");
  assert.equal(signInReturnPath("/auth/callback?next=/plan/x"), "/studio");
});

// ── where sign-in returns to WHEN AN UNLOCK IS PENDING ─────────────────────────────

test("a pending unlock with a saved plan aims the round-trip at that plan", () => {
  // The full-page legs (OAuth, the email-confirmation link) come back to a FRESH mount.
  // On /studio that mount is the wizard — no run, no packs, nothing for the unlock dialog
  // to stand on. /plan/<id> can rebuild all of it from the id, so that is where the
  // round-trip must land, regardless of which page the click happened on.
  const pending = { domain: "example.com", packIndex: 3, planStateId: "abc123XY", savedAt: NOW };
  assert.equal(unlockReturnPath("/studio", pending), "/plan/abc123XY");
  assert.equal(unlockReturnPath("/plan/abc123XY", pending), "/plan/abc123XY");
});

test("no pending unlock (or no saved plan on it) falls back to the plain path rule", () => {
  assert.equal(unlockReturnPath("/studio", null), "/studio");
  assert.equal(
    unlockReturnPath("/studio", { domain: "example.com", packIndex: 2, savedAt: NOW }),
    "/studio",
  );
  assert.equal(unlockReturnPath("//evil.example.com", null), "/studio");
});

test("a hand-edited planStateId cannot steer the redirect anywhere but /plan/<token>", () => {
  // The id is interpolated into a path; anything that is not the URL-safe token
  // plan_state.new_id() mints must fall back rather than build a crafted URL.
  for (const bad of ["../admin", "x/../../etc", "https://evil", "a b", "", "short", "x".repeat(70)]) {
    const pending = { domain: "d", packIndex: null, planStateId: bad, savedAt: NOW };
    assert.equal(unlockReturnPath("/studio", pending), "/studio", JSON.stringify(bad));
  }
});
