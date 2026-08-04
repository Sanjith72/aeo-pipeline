// Unit tests for the /auth/callback decision logic (lib/authCallback.ts). Runs on Node's
// built-in test runner with native TS type-stripping:
//
//   node --test lib/authCallback.test.ts        (or: npm test, from web/)
//
// These cover the part that was actually broken: which SHAPE a callback URL is, and what a
// given failure means. The route component is thin wiring around this module — the project
// has no DOM/React test environment, so putting the branching here is what makes it testable
// at all rather than asserted by eye.

import test from "node:test";
import assert from "node:assert/strict";

import {
  RESEND_COOLDOWN_SEC,
  classifyAuthFailure,
  classifySignUpFailure,
  parseParams,
  readCallback,
  resendCooldownRemaining,
  safeNext,
} from "./authCallback.ts";

const ORIGIN = "https://aeo-studio-nine.vercel.app";

// ── shape detection ────────────────────────────────────────────────────────────────

test("the email confirm link is recognised as token_hash", () => {
  // The exact shape the Supabase template now sends:
  //   {{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email&next=/studio
  const shape = readCallback("?token_hash=abc123&type=email&next=/studio", "");
  assert.equal(shape.kind, "token_hash");
  assert.equal(shape.kind === "token_hash" && shape.tokenHash, "abc123");
  assert.equal(shape.kind === "token_hash" && shape.type, "email");
});

test("token_hash wins over a stray code param", () => {
  // Ordering is load-bearing: falling through to the PKCE branch is precisely the bug —
  // it waits for a session no exchange will produce and then times out after 15s.
  const shape = readCallback("?token_hash=abc&type=signup&code=xyz", "");
  assert.equal(shape.kind, "token_hash");
});

test("an unknown otp type degrades to 'email' rather than being passed through", () => {
  const shape = readCallback("?token_hash=abc&type=wat", "");
  assert.equal(shape.kind === "token_hash" && shape.type, "email");
});

test("every real otp type survives", () => {
  for (const t of ["signup", "invite", "magiclink", "recovery", "email_change", "email"]) {
    const shape = readCallback(`?token_hash=abc&type=${t}`, "");
    assert.equal(shape.kind === "token_hash" && shape.type, t, `type=${t}`);
  }
});

test("the PKCE OAuth return is recognised as code (the working Google leg)", () => {
  assert.equal(readCallback("?code=pkce-code-here", "").kind, "code");
});

test("the legacy implicit fragment is recognised", () => {
  assert.equal(readCallback("", "#access_token=eyJ&refresh_token=r&type=signup").kind,
    "implicit_tokens");
});

test("an empty callback is 'none', not a false positive", () => {
  assert.equal(readCallback("", "").kind, "none");
  assert.equal(readCallback("?next=/studio", "").kind, "none");
  assert.equal(readCallback(null, null).kind, "none");
});

test("a query-string error beats every other shape", () => {
  const shape = readCallback("?error=access_denied&error_description=User+denied&code=x", "");
  assert.equal(shape.kind, "provider_error");
  assert.equal(shape.kind === "provider_error" && shape.description, "User denied");
});

test("a FRAGMENT error is read too", () => {
  // GoTrue reports implicit-flow failures in the fragment, which the previous implementation
  // never looked at — so an expired link arriving this way showed the generic 15s timeout.
  const shape = readCallback("", "#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired");
  assert.equal(shape.kind, "provider_error");
  assert.equal(shape.kind === "provider_error" && shape.code, "otp_expired");
});

test("parseParams tolerates the leading marker, empties and nulls", () => {
  assert.deepEqual(parseParams("?a=1&b=2"), { a: "1", b: "2" });
  assert.deepEqual(parseParams("#a=1"), { a: "1" });
  assert.deepEqual(parseParams("a=1"), { a: "1" });
  assert.deepEqual(parseParams(""), {});
  assert.deepEqual(parseParams("?"), {});
  assert.deepEqual(parseParams(null), {});
});

// ── open-redirect guard ────────────────────────────────────────────────────────────

test("safeNext keeps same-origin paths", () => {
  assert.equal(safeNext("/studio", ORIGIN), "/studio");
  assert.equal(safeNext("/studio?tab=plan#x", ORIGIN), "/studio?tab=plan#x");
});

test("safeNext refuses every off-origin form", () => {
  // "/\r/evil.com" is the interesting one and the reason the guard resolves rather than
  // pattern-matches: the URL parser strips the raw CR *before* parsing, turning it into
  // "//evil.com", so it slips past a startsWith("/") + !startsWith("//") check written
  // against the literal string.
  for (const evil of [
    "https://evil.com/",
    "//evil.com",
    "/\\/evil.com",
    "/\r/evil.com",
    "javascript:alert(1)",
  ]) {
    assert.equal(safeNext(evil, ORIGIN), "/", `should refuse ${JSON.stringify(evil)}`);
  }
});

test("control characters that resolve on-origin stay on-origin", () => {
  // The counterparts to the case above: a tab, a newline or a single backslash is also
  // stripped/normalised, but lands on a harmless same-origin PATH. Asserting these return
  // "/" would be asserting a behaviour the guard does not (and need not) have. What must
  // hold is the invariant, so that is what is checked.
  for (const raw of ["/\\evil.com", "/\tevil.com", "/\nevil.com", "/studio", "/plan/12?a=1"]) {
    const out = safeNext(raw, ORIGIN);
    assert.equal(new URL(out, ORIGIN).origin, ORIGIN, `${JSON.stringify(raw)} left the origin`);
  }
});

test("safeNext falls back to / on empty input", () => {
  assert.equal(safeNext(null, ORIGIN), "/");
  assert.equal(safeNext("", ORIGIN), "/");
});

// ── failure classification ─────────────────────────────────────────────────────────

test("an expired or spent link is classified as spent and offers a resend", () => {
  // The case that matters most: mail scanners burn single-use links before the human
  // clicks, and to the user that is indistinguishable from a broken app.
  for (const msg of [
    "Email link is invalid or has expired",
    "Token has expired or is invalid",
    "otp_expired",
    "This link has already been used",
  ]) {
    const f = classifyAuthFailure(msg);
    assert.equal(f.kind, "spent", msg);
    assert.equal(f.canResend, true, msg);
  }
});

test("an already-confirmed account is not reported as an error to fix", () => {
  const f = classifyAuthFailure("Email address already confirmed");
  assert.equal(f.kind, "already_confirmed");
  assert.equal(f.canResend, false, "resending would not help and invites a loop");
});

test("a rate limit does not offer a resend", () => {
  const f = classifyAuthFailure("email rate limit exceeded");
  assert.equal(f.kind, "rate_limited");
  assert.equal(f.canResend, false);
});

test("a non-allowlisted redirect is named as an operator problem", () => {
  // The invisible one: GoTrue discards a non-allowlisted redirect_to and bounces to Site
  // URL. Telling the user to try again would send them round the same loop forever.
  const f = classifyAuthFailure("redirect_to is not allowed");
  assert.equal(f.kind, "not_allowlisted");
  assert.equal(f.canResend, false);
});

test("an unrecognised message is passed through rather than mislabelled", () => {
  const f = classifyAuthFailure("Some brand new GoTrue wording");
  assert.equal(f.kind, "unknown");
  assert.match(f.message, /Some brand new GoTrue wording/);
});

test("an empty error still produces usable copy", () => {
  assert.ok(classifyAuthFailure(null).message.length > 0);
  assert.ok(classifyAuthFailure("").title.length > 0);
});

test("'User already registered' reads as an account, not an error", () => {
  const f = classifySignUpFailure("User already registered");
  assert.equal(f.kind, "already_confirmed");
  assert.match(f.message, /sign in/i);
  assert.equal(f.canResend, false);
});

test("a weak-password rejection keeps the server's own wording", () => {
  const f = classifySignUpFailure("Password should be at least 6 characters");
  assert.match(f.message, /6 characters/);
});

// ── resend cooldown ────────────────────────────────────────────────────────────────

test("no cooldown before anything has been sent", () => {
  assert.equal(resendCooldownRemaining(null, 1_000_000), 0);
});

test("the cooldown counts down and reaches zero exactly at the limit", () => {
  const t0 = 1_000_000;
  assert.equal(resendCooldownRemaining(t0, t0), RESEND_COOLDOWN_SEC);
  assert.equal(resendCooldownRemaining(t0, t0 + 30_000), RESEND_COOLDOWN_SEC - 30);
  assert.equal(resendCooldownRemaining(t0, t0 + RESEND_COOLDOWN_SEC * 1000), 0);
});

test("the cooldown never goes negative", () => {
  assert.equal(resendCooldownRemaining(1_000_000, 9_999_999), 0);
});

test("the default cooldown is at least Supabase's own one-per-minute limit", () => {
  // A shorter window only produces a rate-limit error the user cannot act on.
  assert.ok(RESEND_COOLDOWN_SEC >= 60);
});
