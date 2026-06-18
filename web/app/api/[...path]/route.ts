import { type NextRequest, NextResponse } from "next/server";

// Server-side API proxy. The browser calls this SAME-ORIGIN route; it injects the secret
// API key and forwards to the real backend — so the key (and the backend URL) never ship in
// the client bundle. This replaces the old browser-visible NEXT_PUBLIC_API_KEY, which was a
// filter, not real auth. Both vars below are server-only (NOT NEXT_PUBLIC):
//   API_BASE_URL  — the backend origin (e.g. http://api:8000 locally, the Railway URL in prod)
//   API_KEY       — forwarded as X-API-Key (matches the backend's AEO__API__AUTH_KEY)
const BACKEND = (process.env.API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");
const API_KEY = process.env.API_KEY ?? "";

export const dynamic = "force-dynamic"; // never cache proxied API responses
export const maxDuration = 300; // allow slow backend calls (e.g. deliverables); capped by the Vercel plan

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const target = `${BACKEND}/api/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length"); // fetch recomputes it for the buffered body
  if (API_KEY) headers.set("X-API-Key", API_KEY);

  const method = req.method.toUpperCase();
  const body = method === "GET" || method === "HEAD" ? undefined : await req.arrayBuffer();

  let res: Response;
  try {
    res = await fetch(target, { method, headers, body, redirect: "manual", cache: "no-store" });
  } catch (e) {
    return NextResponse.json(
      { detail: `proxy could not reach the API at ${BACKEND}: ${(e as Error).message}` },
      { status: 502 },
    );
  }

  // Stream the backend response straight back (JSON, zip downloads, …). Drop hop-by-hop /
  // length headers so the platform re-frames the (possibly decompressed) stream correctly.
  const out = new Headers(res.headers);
  out.delete("content-encoding");
  out.delete("transfer-encoding");
  out.delete("content-length");
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
}

async function handler(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  return proxy(req, path ?? []);
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
