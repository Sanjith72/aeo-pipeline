// Typed client for the AEO HTTP API (SP-4a). Every call maps to one endpoint;
// no business logic lives here. Base URL from NEXT_PUBLIC_API_BASE.

import type {
  AuditJob,
  BriefPlan,
  BriefRequest,
  CompetitorSuggestResponse,
  DeliverablesResponse,
  ProfileResponse,
  SiteReportResponse,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (API_KEY) h["X-API-Key"] = API_KEY;
  return h;
}

// ── instrumentation (Block F) ──────────────────────────────────────────────
// A stable, browser-minted session id persisted in a cookie (NOT localStorage):
// the unit DAU and return-rate are computed over server-side. No third-party SDK.
const SESSION_COOKIE = "aeo_sid";
const LAST_SEEN_COOKIE = "aeo_seen";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365; // ~1 year

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : null;
}

function writeCookie(name: string, value: string): void {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=${encodeURIComponent(value)}; path=/; max-age=${COOKIE_MAX_AGE}; SameSite=Lax`;
}

function getSessionId(): string {
  if (typeof document === "undefined") return "ssr";
  let sid = readCookie(SESSION_COOKIE);
  if (!sid) {
    sid =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `s_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    writeCookie(SESSION_COOKIE, sid);
  }
  return sid;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error(`Cannot reach the API at ${BASE}. Is it running?  (aeo serve)`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  return (await res.json()) as T;
}

export const api = {
  base: BASE,
  async health(): Promise<{ status: string; db: string }> {
    const res = await fetch(`${BASE}/api/health`, { headers: headers() });
    return (await res.json()) as { status: string; db: string };
  },
  plan(req: BriefRequest): Promise<BriefPlan> {
    return postJson<BriefPlan>("/api/plan", req);
  },
  deliverables(
    req: BriefRequest & { draft_limit?: number; builder_mode?: string },
  ): Promise<DeliverablesResponse> {
    return postJson<DeliverablesResponse>("/api/deliverables", req);
  },
  // NOTE: the server also offers POST /api/deliverables.zip, but the UI zips the
  // already-fetched assets client-side instead — the endpoint re-generates the whole
  // bundle (minutes of LLM work) for bytes the browser already has.
  profile(req: { domain: string; use_llm?: boolean; max_urls?: number }): Promise<ProfileResponse> {
    return postJson<ProfileResponse>("/api/profile", req);
  },
  suggestCompetitors(req: {
    name: string;
    domain?: string;
    category?: string;
    location?: string;
    count?: number;
  }): Promise<CompetitorSuggestResponse> {
    return postJson<CompetitorSuggestResponse>("/api/competitors/suggest", req);
  },
  // `force` (R2-2) bypasses the fingerprint skip gate so unchanged pages are re-read.
  startAudit(req: { domain: string; name?: string; force?: boolean }): Promise<{ job_id: string; status: string }> {
    return postJson<{ job_id: string; status: string }>("/api/audit", req);
  },
  async auditStatus(jobId: string): Promise<AuditJob> {
    const res = await fetch(`${BASE}/api/audit/${jobId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as AuditJob;
  },
  /** Cooperatively cancel a running audit (R2-2 drop-off safety). Best-effort: the
   *  audit early-exits between pages, keeping whatever it already analyzed. */
  async cancelAudit(jobId: string): Promise<AuditJob> {
    const res = await fetch(`${BASE}/api/audit/${jobId}/cancel`, {
      method: "POST",
      headers: headers(),
    });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as AuditJob;
  },
  async siteReport(runId: number): Promise<SiteReportResponse> {
    const res = await fetch(`${BASE}/api/site-report/${runId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as SiteReportResponse;
  },

  // ── instrumentation (Block F) ─────────────────────────────────────────────
  /** Fire-and-forget a product-analytics event. Best-effort: failures are swallowed
   *  so analytics can never break the user's flow. `keepalive` lets it survive an
   *  in-flight page unload (e.g. the last wizard step). */
  track(eventType: string, metadata?: Record<string, unknown>, url?: string): void {
    if (typeof window === "undefined") return; // no-op during SSR
    try {
      void fetch(`${BASE}/api/events`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: getSessionId(),
          event_type: eventType,
          url: url ?? null,
          metadata: metadata ?? {},
        }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* best-effort */
    }
  },

  /** Capture a human override (R2-4) — an edited prefill or a rejected recommendation.
   *  Fire-and-forget + best-effort: the server logs it as eval signal AND a human-gated
   *  PROPOSED refinement (never auto-applied). Failures are swallowed so it can't break
   *  the user's flow. */
  captureOverride(
    field: string,
    oldValue: unknown,
    newValue: unknown,
    kind: "field_override" | "recommendation_rejected" = "field_override",
    url?: string,
  ): void {
    if (typeof window === "undefined") return;
    try {
      void fetch(`${BASE}/api/overrides`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          session_id: getSessionId(),
          field,
          old_value: oldValue ?? null,
          new_value: newValue ?? null,
          kind,
          url: url ?? null,
        }),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* best-effort */
    }
  },

  /** Call once on app load: emits `session_start` on a brand-new session and
   *  `return_visit` when an existing session spans a new calendar day — the signal
   *  behind the return-rate metric. */
  trackVisit(): void {
    if (typeof window === "undefined") return;
    const today = new Date().toISOString().slice(0, 10);
    const lastSeen = readCookie(LAST_SEEN_COOKIE);
    if (!lastSeen) this.track("session_start");
    else if (lastSeen !== today) this.track("return_visit", { last_seen: lastSeen });
    writeCookie(LAST_SEEN_COOKIE, today);
  },
};
