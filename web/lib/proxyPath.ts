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
// FIRST FIX (insufficient — recorded because the reasoning was wrong in an instructive way):
// refuse any path containing a literal `.` or `..` segment, on the argument that this keeps
// the checked string byte-identical to the sent string and avoids "normalise, then check".
//
// It did not, because the guard never sees the wire path. Vercel/Next percent-decode the
// catch-all segments roughly twice before this function is called, so a request for
// `/api/entitlements/%25252e/grant` arrives here as the literal segment `"%2e"` — which is not
// `"."`, so layer 1 waves it through — and `fetch()` then performs the final decode and strips
// it. Reproduced against production on 2026-08-07, with the decode-depth ladder showing the
// gap is exactly one decode beyond the guard:
//
//   entitlements/./grant       -> 404 not found      (guard sees ".")
//   entitlements/%2e/grant     -> 404 not found      (guard sees ".")
//   entitlements/%252e/grant   -> 404 not found      (guard sees ".")
//   entitlements/%25252e/grant -> 403 admin credential required   <- REACHED THE GRANT ROUTE
//   x/%25252e%25252e/%25252e%25252e/openapi.json -> 200, full OpenAPI spec
//
// The last line is the worse one: `..` climbs out of `/api/` altogether, so the proxy was an
// authenticated reader of any route on the backend origin.
//
// So the first fix raised the bar from one encoding layer to three and left the CLASS intact:
// a guard that decides on a string other than the one it sends is wrong however carefully the
// string is inspected. The durable fix is to run the same parser `fetch()` will, decide on its
// output, and forward its output — then there is no second string to disagree with.

/** Paths the proxy must never forward, as normal (dot-free) segment strings. */
export const BLOCKED_PATHS: ReadonlySet<string> = new Set(["entitlements/grant"]);

export type ProxyPathDecision =
  | { ok: true; path: string }
  /** `reason` is for the SERVER LOG only. The caller answers 404 either way, so a prober
   *  cannot tell "this route is denylisted" from "that path is malformed". */
  | { ok: false; reason: string };

/** Origin used only to run the same URL parser `fetch()` will. Never contacted. */
const NORMALISE_BASE = "https://proxy.invalid";

/**
 * Decide whether these route segments may be forwarded, and return the exact path to forward.
 *
 * Pure so the rule is pinned by tests rather than by reading a route handler that cannot be
 * unit-tested without a Next request harness.
 *
 * TWO layers, and the second is the one that actually closes the class. Refusing literal
 * dot-segments (layer 1) was the first fix, and it was NOT enough — see the block comment
 * above for how triple-encoding walked straight past it. The durable rule is layer 2:
 * normalise with the SAME parser `fetch()` uses, decide on that, and forward that. Then the
 * string that is checked and the string that is sent cannot differ, whatever encoding games
 * arrive at the door.
 */
export function resolveProxyPath(segments: readonly string[]): ProxyPathDecision {
  // Layer 1 — literal dot-segments. Redundant with layer 2, kept for the precise log line and
  // because a guard whose only defence is "the parser will handle it" is one refactor from
  // being wrong again.
  for (const seg of segments) {
    if (seg === "." || seg === "..") {
      return { ok: false, reason: `dot-segment in path: ${segments.join("/")}` };
    }
  }
  // Empty segments come from `//` or a trailing slash. They change nothing about which route
  // the backend resolves, but they DO change `join("/")`.
  const joined = segments.filter((s) => s !== "").join("/");

  // Layer 2 — normalise exactly as the WHATWG URL parser will. It treats `%2e` / `%2E` as a
  // single-dot segment and `%2e%2e` (and `.%2e`, `%2e.`) as a double-dot segment, decoding and
  // REMOVING them. Verified in node:
  //     new URL("/api/entitlements/%2e/grant", base).pathname  -> "/api/entitlements/grant"
  //     new URL("/api/x/%2e%2e/%2e%2e/openapi.json", base).pathname -> "/openapi.json"
  // Both were reachable in production. The second is the dangerous one: it leaves `/api/`
  // entirely, so the proxy became an authenticated reader of any route on the backend origin
  // (`/openapi.json` and `/docs` both returned 200 through it).
  let pathname: string;
  try {
    pathname = new URL(`/api/${joined}`, NORMALISE_BASE).pathname;
  } catch {
    return { ok: false, reason: `unparseable path: ${joined}` };
  }
  if (pathname !== "/api" && !pathname.startsWith("/api/")) {
    return { ok: false, reason: `path escapes /api/: ${joined} resolves to ${pathname}` };
  }
  const path = pathname === "/api" ? "" : pathname.slice("/api/".length);
  if (BLOCKED_PATHS.has(path)) {
    return { ok: false, reason: `denylisted path: ${path} (from ${joined})` };
  }
  // Return the NORMALISED path, not the input. Re-parsing it is idempotent, so what the
  // denylist just approved is byte-for-byte what fetch() will send.
  return { ok: true, path };
}
