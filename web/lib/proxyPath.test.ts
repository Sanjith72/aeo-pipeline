// Unit tests for the API proxy's path guard (lib/proxyPath.ts).
//
//   node --test lib/proxyPath.test.ts        (or: npm test, from web/)
//
// These pin a live security defect found by the Phase 6 verification pass and reproduced
// against production on 2026-08-07: the denylist checked `path.join("/")` while the forwarded
// URL was built and handed to fetch(), whose URL parser strips dot-segments. So
// `entitlements/./grant` passed the check and arrived at the backend as `entitlements/grant`.
// The backend's own require_admin_key still refused it (403), so the paywall did not open —
// but the proxy layer that exists to keep the admin surface off the public origin was not
// doing its job.

import test from "node:test";
import assert from "node:assert/strict";

import { BLOCKED_PATHS, resolveProxyPath } from "./proxyPath.ts";

const allowed = (segs: string[]) => {
  const d = resolveProxyPath(segs);
  return d.ok ? d.path : null;
};

// ── ordinary traffic still flows ──────────────────────────────────────────────────

test("a normal path is forwarded unchanged", () => {
  assert.equal(allowed(["packs", "42"]), "packs/42");
  assert.equal(allowed(["tickets", "42", "2"]), "tickets/42/2");
  assert.equal(allowed(["health"]), "health");
  assert.equal(allowed(["config"]), "config");
});

test("the empty path is allowed through (the backend decides what /api/ means)", () => {
  assert.equal(allowed([]), "");
});

// ── the literal denylist ──────────────────────────────────────────────────────────

test("the entitlement-granting route is refused", () => {
  const d = resolveProxyPath(["entitlements", "grant"]);
  assert.equal(d.ok, false);
  assert.match(d.ok === false ? d.reason : "", /denylisted/);
});

test("a neighbouring entitlements route is NOT collaterally blocked", () => {
  assert.equal(allowed(["entitlements"]), "entitlements");
  assert.equal(allowed(["entitlements", "redeem"]), "entitlements/redeem");
});

// ── the bypass this module exists for ─────────────────────────────────────────────

test("every dot-segment spelling of the blocked path is refused", () => {
  // Each of these reached the backend as `entitlements/grant` in production.
  for (const segs of [
    ["entitlements", ".", "grant"],
    ["entitlements", "x", "..", "grant"],
    [".", "entitlements", "grant"],
    ["entitlements", ".", ".", "grant"],
    ["entitlements", "..", "entitlements", "grant"],
  ]) {
    const d = resolveProxyPath(segs);
    assert.equal(d.ok, false, `should refuse ${segs.join("/")}`);
    assert.match(d.ok === false ? d.reason : "", /dot-segment/);
  }
});

test("dot-segments are refused even on paths that are not denylisted", () => {
  // `..` can climb above the /api/ prefix the target URL is built from, which would turn the
  // proxy into a reader of arbitrary routes on the backend origin. Refuse the shape, not just
  // the destination — otherwise the guard depends on predicting where it resolves to.
  assert.equal(resolveProxyPath(["packs", "..", "..", "admin"]).ok, false);
  assert.equal(resolveProxyPath(["..", "..", "internal"]).ok, false);
  assert.equal(resolveProxyPath(["packs", ".", "42"]).ok, false);
});

test("empty segments cannot smuggle a blocked path past the check", () => {
  // `//` and trailing slashes change join("/") without changing which route the backend
  // resolves — another way to spell a denylisted path.
  const d = resolveProxyPath(["entitlements", "", "grant"]);
  assert.equal(d.ok, false, "entitlements//grant must still be refused");
});

test("a refusal never says WHICH rule fired, in the caller-visible sense", () => {
  // The reason string is for the server log; both refusals carry one, and route.ts answers
  // 404 for either. This test documents the contract so a future edit does not start
  // returning a distinguishable status for the denylist.
  const a = resolveProxyPath(["entitlements", "grant"]);
  const b = resolveProxyPath(["entitlements", ".", "grant"]);
  assert.equal(a.ok, false);
  assert.equal(b.ok, false);
  assert.ok(a.ok === false && a.reason.length > 0);
  assert.ok(b.ok === false && b.reason.length > 0);
});

// ── the denylist itself ───────────────────────────────────────────────────────────

test("the denylist contains the entitlement-minting route", () => {
  assert.ok(BLOCKED_PATHS.has("entitlements/grant"));
});

test("denylist entries are stored dot-free, so they match what is forwarded", () => {
  for (const p of BLOCKED_PATHS) {
    assert.ok(
      !p.split("/").some((s) => s === "." || s === ".." || s === ""),
      `denylist entry "${p}" must be a plain path`,
    );
  }
});
