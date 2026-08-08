// Remembering WHICH pack a signed-out visitor was trying to unlock, across the sign-in.
//
// The bug: `handleUnlock(packIndex)` in StudioApp did
//
//     if (!user) { openAuth("unlock-pack"); return; }
//
// and `packIndex` went nowhere. `authReason` ("unlock-pack") only selects a line of copy in
// AuthModal; nothing carried the pack. So a visitor clicked Unlock on Pack 3, signed in, and
// landed back staring at the same grid with no dialog and no explanation — the click they
// made had simply been forgotten, and they had to find and click it again. Whether that reads
// as "broken" or "I must have mis-clicked" depends on the user, and neither is what we want
// from someone who was one step from paying.
//
// It has to be PERSISTED rather than held in React state, because one of the two sign-in
// routes is a full page load:
//
//   * email/password — the modal resolves in place, the component never unmounts, and state
//     would have been enough;
//   * Google/OAuth — the browser leaves for the provider and returns to /auth/callback, which
//     router.replace()s onward. Every scrap of in-memory state is gone by then.
//
// Same reasoning, and deliberately the same shape, as lib/checkoutReturn.ts's pending record:
// an intent formed before a redirect, redeemed after it.
//
// What is stored is intent, never entitlement. The pack index decides which dialog to reopen;
// it can never decide what the user owns — that answer only ever comes from re-reading
// entitlements from the server, derived from a verified JWT.

const STORAGE_KEY = "aeo:pending-unlock";

/** Long enough to survive an OAuth round-trip including a password reset or a 2FA prompt;
 *  short enough that a stale intent never reopens a dialog days later. */
const PENDING_TTL_MS = 60 * 60 * 1000;

export interface PendingUnlock {
  domain: string;
  /** null when the click carried no specific pack (the generic "unlock" entry point). */
  packIndex: number | null;
  runId?: number;
  /** Set when the click happened on a resumed plan, so /plan/<id> only redeems its own
   *  intent and one plan's pending unlock can never fire on another. */
  planStateId?: string;
  savedAt: number;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // Safari private mode and friends — a missing memory is not an error here
  }
}

export function rememberPendingUnlock(
  intent: Omit<PendingUnlock, "savedAt">,
  now: number = Date.now(),
  store: Pick<Storage, "setItem"> | null = storage(),
): void {
  try {
    store?.setItem(STORAGE_KEY, JSON.stringify({ ...intent, savedAt: now }));
  } catch {
    /* quota or private mode: the unlock still works, it just will not resume itself */
  }
}

export function readPendingUnlock(
  now: number = Date.now(),
  store: Pick<Storage, "getItem"> | null = storage(),
): PendingUnlock | null {
  try {
    const raw = store?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingUnlock>;
    if (typeof parsed?.savedAt !== "number") return null;
    if (now - parsed.savedAt > PENDING_TTL_MS) return null;
    if (typeof parsed.domain !== "string") return null;
    const packIndex =
      typeof parsed.packIndex === "number" && Number.isInteger(parsed.packIndex) && parsed.packIndex > 0
        ? parsed.packIndex
        : null;
    return {
      domain: parsed.domain,
      packIndex,
      runId:
        typeof parsed.runId === "number" && Number.isInteger(parsed.runId) && parsed.runId > 0
          ? parsed.runId
          : undefined,
      planStateId: typeof parsed.planStateId === "string" ? parsed.planStateId : undefined,
      savedAt: parsed.savedAt,
    };
  } catch {
    return null;
  }
}

export function clearPendingUnlock(store: Pick<Storage, "removeItem"> | null = storage()): void {
  try {
    store?.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do — a stale record expires on its own via the TTL */
  }
}

/**
 * Where should the sign-in round-trip come BACK to?
 *
 * AuthModal hardcoded `?next=/studio` for both email confirmation and OAuth. For a visitor
 * who was reading a saved plan at /plan/<id>, that is a one-way trip: /studio is the wizard,
 * with no run, no packs and no plan in memory, so they arrive somewhere they did not ask to
 * be and their pack is nowhere in sight. Return them to the page they left.
 */
export function signInReturnPath(pathname: string | null | undefined, fallback = "/studio"): string {
  const p = (pathname ?? "").trim();
  // Only same-origin ABSOLUTE PATHS, never a full URL — this value ends up in a redirect,
  // and `next` is attacker-influenceable in the general case. `//evil.com` is a protocol
  // relative URL, not a path, so it is rejected explicitly rather than by prefix luck.
  if (!p.startsWith("/") || p.startsWith("//")) return fallback;
  // The callback and auth routes would bounce the user straight back into sign-in.
  if (p.startsWith("/auth")) return fallback;
  return p;
}
