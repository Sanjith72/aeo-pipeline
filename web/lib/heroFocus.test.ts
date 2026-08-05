// Unit tests for the "back to the hero's URL field" CTA helper (lib/heroFocus.ts).
//
//   node --test lib/heroFocus.test.ts        (or: npm test, from web/)
//
// The bug these pin down: the hero hides its inactive beats with `visibility: hidden`, so
// focusing the domain input at the moment "Get your real report" is clicked does nothing —
// the field is not focusable yet. The cursor never appears and a keyboard user is left with
// focus on an off-screen button.
//
// And the second, subtler one, found in a real Chrome against localhost: the hero's inline
// styles come from a rAF-batched scroll handler and can be a frame (or, in a backgrounded
// tab, indefinitely) STALE — beat 01 was still marked hidden with the page already scrolled
// back to 0. So the loop must retry until the focus actually lands, not until the field looks
// visible.

import test from "node:test";
import assert from "node:assert/strict";

import { FOCUS_TIMEOUT_MS, focusHeroInputWhenReady, nextFocusStep } from "./heroFocus.ts";

// ── the rule, in isolation ─────────────────────────────────────────────────────────

test("a focus that landed ends the loop", () => {
  assert.equal(nextFocusStep({ landed: true, elapsedMs: 0 }), "done");
});

test("a focus that did not land is retried", () => {
  assert.equal(nextFocusStep({ landed: false, elapsedMs: 0 }), "retry");
  assert.equal(nextFocusStep({ landed: false, elapsedMs: FOCUS_TIMEOUT_MS - 1 }), "retry");
});

test("retrying stops at the deadline rather than looping forever", () => {
  assert.equal(nextFocusStep({ landed: false, elapsedMs: FOCUS_TIMEOUT_MS }), "give-up");
  assert.equal(nextFocusStep({ landed: false, elapsedMs: 99_999 }), "give-up");
});

test("a focus that lands past the deadline still counts", () => {
  // `landed` is checked first on purpose: a slow scroll that arrives one frame late should
  // still get the cursor rather than be thrown away.
  assert.equal(nextFocusStep({ landed: true, elapsedMs: FOCUS_TIMEOUT_MS * 10 }), "done");
});

// ── the loop ───────────────────────────────────────────────────────────────────────

/** A manual rAF: collects scheduled callbacks so the test drives the clock frame by frame. */
function pump() {
  const queue: (() => void)[] = [];
  let clock = 0;
  return {
    schedule: (cb: () => void) => void queue.push(cb),
    now: () => clock,
    /** Advance the clock by `ms` and run every callback queued so far. */
    frame(ms = 16) {
      clock += ms;
      queue.splice(0, queue.length).forEach((cb) => cb());
    },
    pending: () => queue.length,
  };
}

/** Stands in for the input: focus only "takes" once the hero beat is showing it. */
function fakeInput() {
  return { attempts: 0, landed: 0, focusable: false };
}
const tryFocus = (el: ReturnType<typeof fakeInput>) => {
  el.attempts += 1;
  if (!el.focusable) return false; // exactly what a visibility:hidden field does
  el.landed += 1;
  return true;
};

test("focus is attempted every frame and sticks the frame the field accepts it", () => {
  const p = pump();
  const el = fakeInput();
  focusHeroInputWhenReady({ find: () => el, tryFocus, now: p.now, schedule: p.schedule });

  p.frame(); // still scrolling — hero beat 01 is visibility:hidden, focus refused
  p.frame();
  assert.equal(el.landed, 0);
  assert.equal(el.attempts, 2, "keeps trying rather than trusting a style read");

  el.focusable = true; // the scroll arrived and beat 01 is showing again
  p.frame();
  assert.equal(el.landed, 1);
  assert.equal(p.pending(), 0, "the loop stops as soon as the cursor is in the field");

  p.frame();
  assert.equal(el.landed, 1, "and never focuses a second time");
});

test("a stale-hidden beat is retried, not believed — the focus still lands", () => {
  // The real failure from Chrome: the field was focusable well before the hero got round to
  // rewriting its inline styles. A style-based check would have given up here.
  const p = pump();
  const el = fakeInput();
  focusHeroInputWhenReady({ find: () => el, tryFocus, now: p.now, schedule: p.schedule });
  for (let i = 0; i < 9; i += 1) p.frame(16);
  el.focusable = true;
  p.frame(16);
  assert.equal(el.landed, 1);
});

test("a field that never appears stops the loop instead of spinning forever", () => {
  const p = pump();
  focusHeroInputWhenReady({
    find: () => null, // the hero never mounted
    tryFocus: () => true,
    now: p.now,
    schedule: p.schedule,
    timeoutMs: 100,
  });
  for (let i = 0; i < 50 && p.pending(); i += 1) p.frame(16);
  assert.equal(p.pending(), 0, "gave up rather than scheduling another frame");
});

test("an interrupted scroll gives up quietly", () => {
  const p = pump();
  const el = fakeInput(); // never becomes focusable — the user grabbed the wheel
  focusHeroInputWhenReady({
    find: () => el,
    tryFocus,
    now: p.now,
    schedule: p.schedule,
    timeoutMs: 100,
  });
  for (let i = 0; i < 50 && p.pending(); i += 1) p.frame(16);
  assert.equal(el.landed, 0);
  assert.equal(p.pending(), 0);
});
