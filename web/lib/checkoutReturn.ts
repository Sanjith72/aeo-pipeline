// v5 CH-02b — the checkout RETURN leg: remembering where a buyer was before Stripe, and
// deciding what to do when they come back.
//
// The bug this exists for: `success_path` is "/studio?checkout=success", and nothing in the
// app ever read that. A buyer paid, Stripe redirected them, and /studio rendered its normal
// empty state — no confirmation, no run, no pack, no error. Indistinguishable from the
// payment having failed, for a payment that succeeded.
//
// Two independent problems, both handled here:
//
//  1. The studio's in-memory run is GONE. A checkout return is a full page load (often in a
//     new tab), so `domain` / `runId` / which pack was being bought have to survive out of
//     band. We persist them before the redirect AND carry run_id + pack through Stripe's
//     metadata onto the success_url, so the restore still works in a browser that never saw
//     the storage — a different tab, a different device, or cleared storage.
//
//  2. The grant is ASYNC. The entitlement is written by Stripe's webhook, not by the browser
//     returning, and a fast redirect regularly beats the webhook. Reading packs once and
//     showing "still locked" would be wrong-but-confident, so the UI polls with backoff and,
//     if it still has not landed, says so honestly with a Refresh rather than a silent blank.
//
// The storage/parsing/backoff rules live here as pure functions so they can be tested under
// this project's runner (node --test, no DOM); StudioApp keeps only the effect wiring.

const STORAGE_KEY = "aeo:pending-checkout";
/** Anything older than this is a stale artefact of an abandoned checkout, not a return in
 *  progress — Stripe sessions expire in 24h and a buyer who was going to come back has. */
const PENDING_TTL_MS = 24 * 60 * 60 * 1000;

export interface PendingCheckout {
  domain: string;
  packIndex: number;
  runId?: number;
  /** Epoch ms, for the TTL above. */
  savedAt: number;
}

export type CheckoutOutcome =
  | { kind: "success"; packIndex?: number; runId?: number }
  | { kind: "cancelled" }
  | { kind: "none" };

/**
 * What does this URL say about a checkout return? Reads ONLY the query Stripe sends us back
 * with (`?checkout=success|cancelled`, plus the `pack` / `run_id` we appended to success_url).
 *
 * Both values are untrusted display hints. They decide which pack to open and which run to
 * reload — never whether anything is unlocked. That answer comes from re-reading entitlements
 * from the server, which is derived from the webhook's grant against the verified JWT.
 */
export function readCheckoutOutcome(search: string | null | undefined): CheckoutOutcome {
  if (!search) return { kind: "none" };
  const q = new URLSearchParams(search.replace(/^\?/, ""));
  const state = q.get("checkout");
  if (state === "cancelled" || state === "canceled") return { kind: "cancelled" };
  if (state !== "success") return { kind: "none" };
  return {
    kind: "success",
    packIndex: toPositiveInt(q.get("pack")),
    runId: toPositiveInt(q.get("run_id")),
  };
}

function toPositiveInt(raw: string | null): number | undefined {
  if (!raw) return undefined;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

/** Persist the pre-checkout context. Best-effort: Safari private mode and storage-disabled
 *  browsers throw on setItem, and failing to remember must never block the sale — the
 *  success_url query is the fallback. */
export function rememberPendingCheckout(
  input: { domain: string; packIndex: number; runId?: number },
  now: number = Date.now(),
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">,
): void {
  const store = storage ?? safeStorage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify({ ...input, savedAt: now } satisfies PendingCheckout));
  } catch {
    /* storage unavailable — the success_url still carries pack + run_id */
  }
}

/** Read back the pre-checkout context, or null when absent, unparseable or stale. */
export function readPendingCheckout(
  now: number = Date.now(),
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">,
): PendingCheckout | null {
  const store = storage ?? safeStorage();
  if (!store) return null;
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingCheckout>;
    if (typeof parsed?.domain !== "string" || !parsed.domain) return null;
    if (!Number.isInteger(parsed.packIndex) || (parsed.packIndex as number) < 1) return null;
    if (typeof parsed.savedAt !== "number" || now - parsed.savedAt > PENDING_TTL_MS) return null;
    return {
      domain: parsed.domain,
      packIndex: parsed.packIndex as number,
      runId: Number.isInteger(parsed.runId) ? (parsed.runId as number) : undefined,
      savedAt: parsed.savedAt,
    };
  } catch {
    return null; // corrupt entry is the same as no entry
  }
}

export function clearPendingCheckout(
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">,
): void {
  const store = storage ?? safeStorage();
  try {
    store?.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do */
  }
}

function safeStorage(): Storage | null {
  // Accessing localStorage THROWS (not returns null) when cookies are blocked.
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Delays between entitlement polls after a successful return, in ms.
 *
 * Backoff rather than a fixed interval: the webhook usually lands within a second or two, so
 * the first checks are quick and the common case feels instant; the later gaps stretch out so
 * a slow webhook does not turn into ~20 hammering requests. Sums to ~20s over 8 attempts,
 * which is the window the doc asks for and comfortably longer than a healthy Stripe delivery.
 */
export const POLL_DELAYS_MS: readonly number[] = [400, 700, 1200, 1800, 2600, 3600, 4700, 5000];

/** Total time the poll will wait before giving up — exported so the UI copy and the schedule
 *  can never drift apart ("about 20 seconds" must stay true if the schedule changes). */
export const POLL_TOTAL_MS = POLL_DELAYS_MS.reduce((a, b) => a + b, 0);

/** Is this pack unlocked according to a freshly-fetched pack list? Returns false when the
 *  pack is absent, so a run whose packs have not persisted yet reads as "not yet", never as
 *  a spurious success. */
export function isPackUnlocked(
  packs: ReadonlyArray<{ pack_index: number; locked?: boolean }> | null | undefined,
  packIndex: number,
): boolean {
  const hit = packs?.find((p) => p.pack_index === packIndex);
  return hit ? hit.locked === false : false;
}

/** Strip the checkout params from the URL, preserving everything else. Returned as a path so
 *  the caller can history.replaceState it — a refresh must not re-run the whole flow, and the
 *  user should not be left staring at ?checkout=success in the address bar. */
export function urlWithoutCheckoutParams(pathname: string, search: string, hash = ""): string {
  const q = new URLSearchParams(search.replace(/^\?/, ""));
  for (const k of ["checkout", "pack", "run_id", "session_id"]) q.delete(k);
  const rest = q.toString();
  return `${pathname}${rest ? `?${rest}` : ""}${hash}`;
}

// ── where the acknowledgement points ───────────────────────────────────────────────
//
// The notice above was written assuming the buyer lands on the results view, and its copy
// says "It's open below." It was rendered inside StudioApp's `view === "results"` branch —
// and NOTHING on the checkout return path sets `view`. A Stripe return is a fresh page load,
// so `view` is its initial "wizard", and every branch of the notice (confirming, unlocked,
// pending_grant, unknown_run, cancelled) rendered into a subtree that was not on screen.
//
// The buyer therefore paid, was redirected, and saw the studio wizard at step 01 with no
// acknowledgement of any kind — the exact symptom the checkout-return work was written to
// fix. Worse, the query params are stripped before that, so a refresh cannot re-trigger the
// flow, and the obvious next action (fill in the wizard, press "Build my plan") starts a
// SECOND audit and spends another crawl+LLM slot.
//
// So the notice moves out of the results branch and renders in either view. Which means the
// copy can no longer assume the pack grid is on screen — hence this: say "below" only when
// it really is below, otherwise point at the saved plan, and when there is neither, say that
// plainly rather than gesturing at something the buyer cannot see.

export type UnlockedDestination =
  /** The pack grid is on screen right now — "it's open below" is literally true. */
  | { kind: "below" }
  /** Not on screen, but this browser has a saved plan the pack belongs to. */
  | { kind: "plan"; href: string }
  /** Neither. The grant is real; we just cannot show them where. Do not invent a link. */
  | { kind: "unknown" };

export function unlockedDestination(opts: {
  /** Is the pack grid actually rendered right now? */
  packsVisible: boolean;
  /** A persisted plan id for this buyer, from the current run or a prior session. */
  planId?: string | null;
}): UnlockedDestination {
  if (opts.packsVisible) return { kind: "below" };
  const id = (opts.planId ?? "").trim();
  return id ? { kind: "plan", href: `/plan/${id}` } : { kind: "unknown" };
}
