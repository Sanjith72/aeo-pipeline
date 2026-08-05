// Returning a visitor to the hero's URL field — the landing page's one real entry point.
//
// "Get your real report", under the sample report on the landing page, used to link to
// /studio. That is a different page with none of the hero's context, and it asks the visitor
// to type their domain a second time. The CTA should land on the thing it promises: the
// "yourwebsite.com" field and "Analyze my site" at the top of the page they are already on.
//
// The non-obvious part — and the reason this is a module with tests rather than two lines
// inside the component — is that scrolling is not enough to put the cursor in that field.
// The hero's three beats are crossfaded by scroll position and every inactive beat is set to
// `visibility: hidden` (components/ui/horizon-hero.tsx), deliberately, so a faded-out beat's
// editable input is not a tab stop. Beat 01 is inactive at the scroll position the button is
// clicked from, so calling .focus() there is a silent NO-OP: the scroll happens, the cursor
// does not appear, and a keyboard user's focus is left behind on a button that is now off
// screen.
//
// So the focus has to be RETRIED until it takes. Note "until it takes", not "until the field
// looks visible": the hero writes those inline styles from a rAF-batched scroll handler, so
// its idea of which beat is showing can lag the actual scroll position by a frame — and, in a
// backgrounded tab where rAF is frozen outright, can be arbitrarily stale. Verified in Chrome
// against localhost:3000: with the page scrolled back to 0, beat 01 was still carrying the
// `opacity: 0; visibility: hidden` it was given at the bottom of the hero. Reading computed
// style would have believed that and given up. Attempting the focus and checking whether
// `document.activeElement` actually moved is the ground truth, and it is equally right for
// every other reason a focus can be refused (an `inert` ancestor, `display: none`, the field
// not mounted yet).
//
// It also has to give up rather than retry forever, for the case where the field never
// becomes focusable at all — an interrupted scroll, or a hero that failed to mount.

/** The id on the hero's domain input. One constant so the CTA and the field cannot drift. */
export const HERO_DOMAIN_INPUT_ID = "hero-domain";

/** How long to keep trying before giving up quietly. A smooth scroll back across the hero's
 *  340vh region is comfortably inside this; longer means the scroll was interrupted (the user
 *  grabbed the wheel), and stealing focus then would be hostile. */
export const FOCUS_TIMEOUT_MS = 3000;

export type FocusStep = "done" | "retry" | "give-up";

/**
 * The rule, as a pure function so it is pinned by a test rather than buried in an effect:
 * stop the moment focus has actually landed, keep retrying until the deadline, then stop.
 */
export function nextFocusStep({
  landed,
  elapsedMs,
  timeoutMs = FOCUS_TIMEOUT_MS,
}: {
  landed: boolean;
  elapsedMs: number;
  timeoutMs?: number;
}): FocusStep {
  if (landed) return "done";
  if (elapsedMs >= timeoutMs) return "give-up";
  return "retry";
}

export interface HeroFocusDeps<E> {
  /** Re-looked-up every tick: the hero mounts its canvas lazily and may not be there yet. */
  find: () => E | null;
  /** Attempt the focus. Returns whether it actually landed (activeElement moved). Must use
   *  `preventScroll` so a mid-flight smooth scroll is not cut short by the browser jumping
   *  the field into view. */
  tryFocus: (el: E) => boolean;
  /** Monotonic ms. */
  now: () => number;
  /** requestAnimationFrame in the browser; a manual pump in tests. */
  schedule: (cb: () => void) => void;
  timeoutMs?: number;
}

/**
 * Put the cursor in the hero's domain field once the scroll has actually brought it back.
 * Fire-and-forget: it stops itself on success and at the deadline, so a hero that never
 * appears can never leave a frame loop running.
 */
export function focusHeroInputWhenReady<E>(deps: HeroFocusDeps<E>): void {
  const start = deps.now();
  const tick = () => {
    const el = deps.find();
    const step = nextFocusStep({
      landed: el !== null && deps.tryFocus(el),
      elapsedMs: deps.now() - start,
      timeoutMs: deps.timeoutMs,
    });
    if (step === "retry") deps.schedule(tick);
  };
  deps.schedule(tick);
}
