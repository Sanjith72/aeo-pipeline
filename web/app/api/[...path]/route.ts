import { type NextRequest, NextResponse } from "next/server";

import { resolveProxyPath } from "@/lib/proxyPath";

// Server-side API proxy. The browser calls this SAME-ORIGIN route; it injects the secret
// API key and forwards to the real backend — so the key (and the backend URL) never ship in
// the client bundle. This replaces the old browser-visible NEXT_PUBLIC_API_KEY, which was a
// filter, not real auth. Both vars below are server-only (NOT NEXT_PUBLIC):
//   API_BASE_URL  — the backend origin (e.g. http://api:8000 locally, the Railway URL in prod)
//   API_KEY       — forwarded as X-API-Key (matches the backend's AEO__API__AUTH_KEY)
const BACKEND = (process.env.API_BASE_URL?.trim() || "http://localhost:8000").replace(/\/+$/, "");
const API_KEY = process.env.API_KEY ?? "";

// ── "this deployment has no backend" (Phase 4 item 4.4) ────────────────────────────────
//
// API_BASE_URL and API_KEY are Production-scope only on this Vercel project, so on a PREVIEW
// deployment neither exists and BACKEND silently falls back to http://localhost:8000 — a
// port on the serverless function itself, where nothing is listening. Every /api/* call then
// takes the retry path (three attempts, ~1s of backoff) and returns the generic
// "temporarily unreachable" 502, which is a lie: nothing is temporary and no amount of trying
// again will help. It reads exactly like a backend outage, which is how this cost real
// debugging time.
//
// So: name it. A localhost target on a DEPLOYED Vercel runtime is a configuration error with
// certainty — there is no localhost backend for a Vercel function to reach, ever. Gated on
// VERCEL_ENV being production/preview rather than on VERCEL alone, because `vercel dev` also
// sets VERCEL=1 and there localhost is exactly right. Everywhere else (docker-compose,
// `next start` on a box that really does run the API on :8000) is left alone.
const VERCEL_ENV = process.env.VERCEL_ENV ?? "";
const IS_DEPLOYED = VERCEL_ENV === "production" || VERCEL_ENV === "preview";
const TARGET_IS_LOCALHOST = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])([:/]|$)/i.test(BACKEND);
const NO_BACKEND_CONFIGURED = IS_DEPLOYED && TARGET_IS_LOCALHOST;

/** One-shot server-side log lines: a per-request log on a broken preview is just noise. */
let warnedNoBackend = false;
let warnedNoKey = false;

function warnOnce(): void {
  if (NO_BACKEND_CONFIGURED && !warnedNoBackend) {
    warnedNoBackend = true;
    console.error(
      `[api-proxy] MISCONFIGURED: API_BASE_URL is unset on this ${VERCEL_ENV} deployment, so ` +
        "every /api/* call would target localhost inside the function. Set API_BASE_URL (and " +
        "API_KEY) in Vercel → Settings → Environment Variables with Production + Preview + " +
        "Development all ticked — Production-only scoping is what breaks preview deployments. " +
        "See DEPLOY.md → Environment reference → Web host.",
    );
  }
  if (IS_DEPLOYED && !NO_BACKEND_CONFIGURED && !API_KEY && !warnedNoKey) {
    warnedNoKey = true;
    console.warn(
      `[api-proxy] API_KEY is empty on this ${VERCEL_ENV} deployment. If the backend has ` +
        "AEO__API__AUTH_KEY set (it must, in any public deploy), every proxied call will 401.",
    );
  }
}

export const dynamic = "force-dynamic"; // never cache proxied API responses
export const maxDuration = 300; // allow slow backend calls (e.g. deliverables); capped by the Vercel plan

// Node's fetch (undici) keeps a keep-alive socket pool to the backend. uvicorn closes idle
// connections after its keep-alive window, so a pooled socket can be reused at the exact
// moment the server closes it — the fetch then rejects with "fetch failed" (ECONNRESET). The
// same reject happens when the backend is briefly unreachable (e.g. its event loop is stalled
// by a heavy in-process audit). Both are transient. A rejected fetch means NO response was
// received, so the request never executed on the backend — which makes a retry safe even for
// POST. Retry a few times with small backoff before surfacing a 502.
const MAX_ATTEMPTS = 3;
const RETRY_BACKOFF_MS = [250, 750]; // waited before attempt 2 and attempt 3

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  // Which paths may be forwarded lives in lib/proxyPath.ts, with tests. It refuses the
  // entitlement-minting route (the injected API_KEY authenticates the PROXY, not the person,
  // so any backend route gated only by X-API-Key is effectively public through here) AND any
  // path containing a dot-segment.
  //
  // The dot-segment half is not hypothetical: the old check was `Set.has(path.join("/"))`
  // while the forwarded URL went through fetch()'s URL parser, which strips `.` and `..`. So
  // `entitlements/./grant` passed the check and arrived at the backend as
  // `entitlements/grant`. Reproduced against production on 2026-08-07 (403 "admin credential
  // required" through the proxy, vs 404 for the same raw path sent straight to the backend —
  // proving the proxy did the rewriting). The backend's require_admin_key still refused it,
  // so nothing was granted, but this layer was not doing its job. Refusing dot-segments
  // outright keeps the string that is CHECKED byte-identical to the string that is SENT.
  const decision = resolveProxyPath(path);
  if (!decision.ok) {
    // Same 404 for both refusal kinds, so a prober cannot distinguish "denylisted" from
    // "malformed". The reason goes to the server log only.
    console.warn(`[api-proxy] refused: ${decision.reason}`);
    return NextResponse.json({ detail: "not found" }, { status: 404 });
  }
  const joined = decision.path;
  warnOnce();
  // 503, not the 502 three failed fetches would produce: this is a permanent configuration
  // fault, not an unreachable backend, and retrying cannot fix it. The message names the
  // variable so the fix is obvious from the network tab alone — the backend ORIGIN is still
  // never echoed, which is the whole point of this file existing.
  if (NO_BACKEND_CONFIGURED) {
    return NextResponse.json(
      {
        detail:
          "This deployment has no backend configured (API_BASE_URL is not set for this " +
          "environment). See DEPLOY.md → Environment reference → Web host.",
      },
      { status: 503 },
    );
  }
  const target = `${BACKEND}/api/${joined}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length"); // fetch recomputes it for the buffered body
  if (API_KEY) headers.set("X-API-Key", API_KEY);

  const method = req.method.toUpperCase();
  // Buffer the body once so it can be replayed across retry attempts (a stream could not).
  const body = method === "GET" || method === "HEAD" ? undefined : await req.arrayBuffer();

  let lastErr: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const startedAt = Date.now();
    try {
      const res = await fetch(target, { method, headers, body, redirect: "manual", cache: "no-store" });
      // Stream the backend response straight back (JSON, zip downloads, …). Drop hop-by-hop /
      // length headers so the platform re-frames the (possibly decompressed) stream correctly.
      const out = new Headers(res.headers);
      out.delete("content-encoding");
      out.delete("transfer-encoding");
      out.delete("content-length");
      return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
    } catch (e) {
      lastErr = e;
      // Only retry FAST failures: the keep-alive socket race resets the connection before any
      // bytes flow (a few ms). A failure after a long wait means the backend likely received the
      // request and is still working on it (e.g. a slow build) or undici's headersTimeout fired —
      // replaying would DUPLICATE that work and pile load on (the old "Build my plan" storm). So
      // surface it instead of retrying. (Long jobs now return immediately + poll, so this branch
      // is belt-and-suspenders.)
      const quick = Date.now() - startedAt < 5_000;
      if (!quick || attempt >= MAX_ATTEMPTS) break;
      await sleep(RETRY_BACKOFF_MS[attempt - 1]);
    }
  }

  // Log the target + cause server-side, but never echo them to the browser: this file
  // exists precisely so the backend origin never ships to the client, and the old message
  // put ${BACKEND} straight into the 502 body.
  console.error(
    `[api-proxy] ${method} ${path.join("/")} failed after ${MAX_ATTEMPTS} attempts ` +
      `(target ${target}): ${(lastErr as Error)?.message ?? "fetch failed"}`,
  );
  return NextResponse.json(
    { detail: "The API is temporarily unreachable. Please try again in a moment." },
    { status: 502 },
  );
}

async function handler(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  return proxy(req, path ?? []);
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
