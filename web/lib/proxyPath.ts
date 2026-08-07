// What path is the API proxy actually allowed to forward?
//
// This exists because the denylist in web/app/api/[...path]/route.ts was checking a different
// string from the one that reached the backend, and that gap was exploitable.
//
// The proxy injects the service API key into every request it forwards. Every visitor's
// browser can reach that route same-origin, so any backend route gated only by `X-API-Key` is
// effectively public through the proxy — which is why `entitlements/grant`, the route that
// MINTS entitlements, is refused here as a second layer behind the backend's own
// `require_admin_key`.
//
// The bug: the denylist was a literal `Set.has(path.join("/"))`, but the forwarded URL was
// built as `${BACKEND}/api/${joined}` and handed to `fetch()`, whose WHATWG URL parser
// removes dot-segments. So the check saw `entitlements/./grant` (not in the Set → allowed)
// while the backend received `entitlements/grant`. Verified against production 2026-08-07,
// with `curl --path-as-is` so the client did not normalise first:
//
//   POST /api/entitlements/grant        -> 404 {"detail":"not found"}            (blocked)
//   POST /api/entitlements/./grant      -> 403 {"detail":"admin credential required"}
//   POST /api/entitlements/x/../grant   -> 403  (same)
//   POST /api/./entitlements/grant      -> 403  (same)
//   [control] the SAME raw path sent straight to the backend -> 404 "Not Found",
//   proving the backend does not normalise and the rewrite happened inside the proxy.
//
// The paywall did not actually open — the backend's `require_admin_key` returned 403 — but
// the layer whose whole job is keeping the admin surface off the public origin was not doing
// it. `..` is the worse half: it can also climb ABOVE `/api/`, turning the proxy into a
// general-purpose reader of any route on the backend origin.
//
// The fix is not "normalise, then check" — that just moves the equivalence argument somewhere
// else and invites the next mismatch. It is: REFUSE any path containing a dot-segment at all,
// so the string that is checked is byte-identical to the string that is sent. No legitimate
// route in this API has a `.` or `..` path segment.

/** Paths the proxy must never forward, as normal (dot-free) segment strings. */
export const BLOCKED_PATHS: ReadonlySet<string> = new Set(["entitlements/grant"]);

export type ProxyPathDecision =
  | { ok: true; path: string }
  /** `reason` is for the SERVER LOG only. The caller answers 404 either way, so a prober
   *  cannot tell "this route is denylisted" from "that path is malformed". */
  | { ok: false; reason: string };

/**
 * Decide whether these route segments may be forwarded, and return the exact path to forward.
 *
 * Pure so the rule is pinned by tests rather than by reading a route handler that cannot be
 * unit-tested without a Next request harness.
 */
export function resolveProxyPath(segments: readonly string[]): ProxyPathDecision {
  // Dot-segments are refused outright, before anything else looks at the path. `.` is merely
  // an alias that defeats a literal denylist; `..` additionally escapes the `/api/` prefix the
  // target URL is built from.
  for (const seg of segments) {
    if (seg === "." || seg === "..") {
      return { ok: false, reason: `dot-segment in path: ${segments.join("/")}` };
    }
  }
  // Empty segments come from `//` or a trailing slash. They change nothing about which route
  // the backend resolves, but they DO change `join("/")` — so they are another way to spell a
  // blocked path past a literal Set. Drop them, then check what remains.
  const path = segments.filter((s) => s !== "").join("/");
  if (BLOCKED_PATHS.has(path)) {
    return { ok: false, reason: `denylisted path: ${path}` };
  }
  return { ok: true, path };
}
