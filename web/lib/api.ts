// Typed client for the AEO HTTP API (SP-4a). Every call maps to one endpoint;
// no business logic lives here. Calls are same-origin to the server proxy (app/api/[...path]).

import { getAccessToken } from "./supabase";
import type {
  AgentRunDetail,
  AgentRunSummary,
  AgentStreamMessage,
  AuditJob,
  BriefPlan,
  BriefRequest,
  BundleAsset,
  CompetitorSuggestResponse,
  DeliverablesJob,
  DeliverablesResponse,
  GamificationView,
  MilestoneDashboard,
  MilestoneStatus,
  MilestoneVerifyResult,
  OverviewResponse,
  PackDetailResponse,
  PacksResponse,
  PlanStateResponse,
  ProfileResponse,
  Ticket,
  TicketsResponse,
  RecheckStatusResponse,
  ResumeResponse,
  SharedPlanResponse,
  SiteFreshnessResponse,
  SiteProfile,
  SiteReportResponse,
  StructuredPlan,
} from "./types";

// Same-origin: every call goes to this app's server-side proxy (app/api/[...path]/route.ts),
// which injects the secret X-API-Key and forwards to the real backend. The key never reaches
// the browser — so there is deliberately no NEXT_PUBLIC_API_KEY here anymore.
const BASE = "";

function headers(extra?: Record<string, string>): Record<string, string> {
  // The server proxy adds the SERVICE X-API-Key. Here we attach the USER's Supabase JWT
  // (v5 CH-07) so the backend can bind entitlements to the logged-in user — the proxy
  // forwards Authorization untouched. Anonymous (no session) → no header, so overview /
  // profile stay tokenless-OK and the free tier is unchanged. The token is NOT a security
  // boundary: the backend verifies its signature/aud/role and derives the user only from
  // the verified claim.
  const token = getAccessToken();
  return { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...extra };
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

async function postJson<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      ...init,
    });
  } catch (err) {
    // An AbortSignal.timeout fires a TimeoutError; a manual cancel fires an AbortError.
    if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) {
      throw new Error("That took longer than expected. Please try again.");
    }
    throw new Error(`Cannot reach the API at ${BASE}. Is it running?  (aeo serve)`);
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    // Attach the HTTP status so callers can branch on it (e.g. a 422 invalid promo code vs
    // a transient failure) — mirrors the status-carrying errors from me()/getPackDetail().
    throw Object.assign(new Error(`API ${res.status} ${res.statusText}${text ? `: ${text}` : ""}`), {
      status: res.status,
    });
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
    init?: RequestInit,
  ): Promise<DeliverablesResponse> {
    return postJson<DeliverablesResponse>("/api/deliverables", req, init);
  },
  // ── personalized downloadable files (#7, async) ───────────────────────────
  /** Start the slow, AI-personalized downloadable-files build as a background job. The
   *  in-app plan is already instant via `deliverables({ use_llm: false })`; this upgrades
   *  the page drafts to AI-written prose. Returns a job id to poll with `personalizeStatus`. */
  startPersonalize(
    req: BriefRequest & { draft_limit?: number; builder_mode?: string },
  ): Promise<{ job_id: string; status: string }> {
    return postJson<{ job_id: string; status: string }>("/api/deliverables/personalize", req);
  },
  /** Poll a personalization job; `result` holds the full DeliverablesResponse on success. */
  async personalizeStatus(jobId: string): Promise<DeliverablesJob> {
    const res = await fetch(`${BASE}/api/deliverables/${encodeURIComponent(jobId)}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as DeliverablesJob;
  },
  // NOTE: the server also offers POST /api/deliverables.zip, but the UI zips the
  // already-fetched assets client-side instead — the endpoint re-generates the whole
  // bundle (minutes of LLM work) for bytes the browser already has.
  profile(req: { domain: string; use_llm?: boolean; max_urls?: number }): Promise<ProfileResponse> {
    return postJson<ProfileResponse>("/api/profile", req);
  },
  /** The v5 free overview (CH-09): fast site scan + five homepage skill scores + a pack
   *  preview + on-site competitor names. No auth; the server caches per domain for a day
   *  (response carries `cached: true` on a hit). */
  overview(req: { domain: string }): Promise<OverviewResponse> {
    return postJson<OverviewResponse>("/api/overview", req);
  },
  suggestCompetitors(req: {
    name: string;
    domain?: string;
    category?: string;
    location?: string;
    services?: string[];
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
  /** The impact-ordered packs persisted for a deep-audit run (CH-03). Empty `packs` for
   *  older/dry-run-only runs — callers fall back to the live overview preview. Auth-aware:
   *  a logged-in user's `locked` flags reflect their real entitlements. */
  async getPacks(runId: number): Promise<PacksResponse> {
    const res = await fetch(`${BASE}/api/packs/${runId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as PacksResponse;
  },
  /** A pack's gated per-page skill detail (CH-02a). 403 when the pack is locked for the
   *  caller (enforced server-side — Pack 1 is free even anonymous). */
  async getPackDetail(runId: number, packIndex: number): Promise<PackDetailResponse> {
    const res = await fetch(`${BASE}/api/packs/${runId}/${packIndex}`, { headers: headers() });
    if (res.status === 403) throw Object.assign(new Error("locked"), { status: 403 });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as PackDetailResponse;
  },

  // ── tickets (v5 CH-08 board + CH-15 before/after) ──────────────────────────
  /** The tickets for a run's packs (lazily generated on first view). */
  async getTickets(runId: number): Promise<TicketsResponse> {
    const res = await fetch(`${BASE}/api/tickets/${runId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as TicketsResponse;
  },
  /** The tickets for one pack of a run. */
  async getPackTickets(runId: number, packIndex: number): Promise<TicketsResponse> {
    const res = await fetch(`${BASE}/api/tickets/${runId}/${packIndex}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as TicketsResponse;
  },
  /** Set a ticket's assignee / target_date (CH-08). Only the flagged fields change. */
  setTicketFields(
    runId: number,
    body: { task_key: string; assignee?: string | null; target_date?: string | null },
  ): Promise<{ ticket: Ticket }> {
    const payload: Record<string, unknown> = { task_key: body.task_key };
    if ("assignee" in body) {
      payload.set_assignee = true;
      payload.assignee = body.assignee ?? null;
    }
    if ("target_date" in body) {
      payload.set_target_date = true;
      payload.target_date = body.target_date ?? null;
    }
    return postJson<{ ticket: Ticket }>(`/api/tickets/${runId}/fields`, payload);
  },
  /** Mark a ticket done (CH-15) → closed_pending_verify + a forced re-crawl to prove the
   *  lift. Poll getPackTickets until the ticket flips to verified_completed. */
  closeTicket(runId: number, taskKey: string): Promise<{ ticket: Ticket; verify_job_id: number | null }> {
    return postJson(`/api/tickets/${runId}/close`, { task_key: taskKey });
  },
  reopenTicket(runId: number, taskKey: string): Promise<{ ticket: Ticket }> {
    return postJson(`/api/tickets/${runId}/reopen`, { task_key: taskKey });
  },
  /** Re-run verification on an already-closed ticket (re-enqueues the forced re-crawl)
   *  without changing its status — the "Recheck" affordance. */
  recheckTicket(runId: number, taskKey: string): Promise<{ ticket: Ticket; verify_job_id: number | null }> {
    return postJson(`/api/tickets/${runId}/recheck`, { task_key: taskKey });
  },

  // ── auth (v5 CH-07) ────────────────────────────────────────────────────────
  /** Confirm the session + provision the user server-side (also claims the aeo_sid session,
   *  cookie-sourced). Requires a valid Supabase JWT; throws on 401. */
  async me(): Promise<{ id: string; email: string | null }> {
    const res = await fetch(`${BASE}/api/auth/me`, { headers: headers() });
    if (!res.ok) throw Object.assign(new Error(`API ${res.status}`), { status: res.status });
    return (await res.json()) as { id: string; email: string | null };
  },
  /** Redeem a promo code to unlock a domain's packs (monetization stub — payments deferred).
   *  Requires login; the backend binds the grant to the verified user, not anything sent. */
  async redeemPromo(domain: string, code: string): Promise<{ unlocked: boolean; domain: string }> {
    return postJson<{ unlocked: boolean; domain: string }>("/api/entitlements/redeem", { domain, code });
  },

  // ── implementation milestones (persisted + auto-verified plan) ────────────
  /** Persist the generated plan as the site's milestones and return the dashboard.
   *  Idempotent — re-syncing keeps existing progress + any crawl-verified status. */
  syncMilestones(req: {
    domain: string;
    name?: string;
    plan: StructuredPlan;
    cms_type?: string | null;
  }): Promise<MilestoneDashboard> {
    return postJson<MilestoneDashboard>("/api/milestones", req);
  },
  /** The dashboard for a domain (empty milestones if no plan persisted yet). */
  async getMilestones(domain: string): Promise<MilestoneDashboard> {
    const res = await fetch(`${BASE}/api/milestones?domain=${encodeURIComponent(domain)}`, {
      headers: headers(),
    });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as MilestoneDashboard;
  },
  /** Owner's manual status toggle for one task; returns the recomputed dashboard. */
  setMilestoneTask(req: { domain: string; task_key: string; status: MilestoneStatus }): Promise<MilestoneDashboard> {
    return postJson<MilestoneDashboard>("/api/milestones/task", req);
  },
  /** Run the verification crawl now ("Check my site") — detects which pending
   *  artifacts are live, auto-verifies them, and returns the refreshed dashboard.
   *  Takes `init` so callers can bound it: this runs a real site crawl, and without an
   *  AbortSignal the spinner was capped only by the proxy's 300s ceiling. */
  verifyMilestones(domain: string, init?: RequestInit): Promise<MilestoneVerifyResult> {
    return postJson<MilestoneVerifyResult>("/api/milestones/verify", { domain }, init);
  },
  /** Revoke the current Developer Handoff link and get a fresh token (owner action,
   *  authenticated). The old /share/<token> link stops working immediately. */
  rotateShareLink(domain: string): Promise<{ share_token: string }> {
    return postJson<{ share_token: string }>("/api/share/rotate", { domain });
  },
  /** The public, read-only shared plan behind a Developer Handoff link. No auth — the
   *  token is the credential. Used by the /share/[token] route. */
  async getSharedPlan(token: string): Promise<SharedPlanResponse> {
    const res = await fetch(`${BASE}/api/share/${encodeURIComponent(token)}`, { headers: headers() });
    if (!res.ok) {
      if (res.status === 404) throw new Error("This share link is invalid or has been revoked.");
      throw new Error(`API ${res.status} ${res.statusText}`);
    }
    return (await res.json()) as SharedPlanResponse;
  },

  // ── persisted, resumable plan (B1, Spec #1) ───────────────────────────────
  /** Persist the interactive plan so progress survives a device switch and earns a
   *  resumable /plan/<id> link. The session id is attached so the homepage can
   *  auto-offer a resume on return. */
  createPlanState(req: {
    plan: StructuredPlan;
    profile?: SiteProfile | null;
    run_id?: number | null;
    business_name?: string | null;
    domain?: string | null;
    score?: number | null;
    done_task_ids?: string[];
  }): Promise<{ id: string }> {
    return postJson<{ id: string }>("/api/plan-state", { session_id: getSessionId(), ...req });
  },
  async getPlanState(id: string): Promise<PlanStateResponse> {
    const res = await fetch(`${BASE}/api/plan-state/${encodeURIComponent(id)}`, { headers: headers() });
    // Attach the status so the caller can tell 404 (gone) from 5xx/network (retry).
    if (!res.ok) throw Object.assign(new Error(`API ${res.status} ${res.statusText}`), { status: res.status });
    return (await res.json()) as PlanStateResponse;
  },
  /** Fire-and-forget progress save: a failed write must never lose the user's check, so
   *  callers also mirror to localStorage. `keepalive` lets the last toggle survive unload. */
  updatePlanState(id: string, body: { done_task_ids: string[]; score?: number | null }): void {
    if (typeof window === "undefined") return;
    try {
      void fetch(`${BASE}/api/plan-state/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
        keepalive: true,
      }).catch(() => {});
    } catch {
      /* best-effort */
    }
  },
  /** The newest saved plan for this browser's session, for the 'resume your plan' banner.
   *  Best-effort: any failure resolves to {id:null} so the homepage never shows an error. */
  async resumePlan(): Promise<ResumeResponse> {
    if (typeof window === "undefined") return { id: null };
    try {
      const res = await fetch(`${BASE}/api/plan-state?session_id=${encodeURIComponent(getSessionId())}`, {
        headers: headers(),
      });
      if (!res.ok) return { id: null };
      return (await res.json()) as ResumeResponse;
    } catch {
      return { id: null };
    }
  },
  /** Has this domain been audited recently? Powers the "Last reviewed N days ago" /
   *  use-existing affordance (Slice 2b). Best-effort: any failure resolves to not-fresh. */
  async siteFreshness(domain: string): Promise<SiteFreshnessResponse> {
    if (typeof window === "undefined" || !domain) return { fresh: false };
    try {
      const res = await fetch(`${BASE}/api/site-freshness?domain=${encodeURIComponent(domain)}`, {
        headers: headers(),
      });
      if (!res.ok) return { fresh: false };
      return (await res.json()) as SiteFreshnessResponse;
    } catch {
      return { fresh: false };
    }
  },
  /** Re-crawl-verified outcomes + predicted pending fixes for a domain — the "Fix impact"
   *  view (Spec #2 "Verified live" · Feature #2). Best-effort: any failure resolves to an
   *  empty set so results never break over it. */
  async recheckStatus(domain: string): Promise<RecheckStatusResponse> {
    if (typeof window === "undefined" || !domain) return { verified: [], pending: [], count: 0 };
    try {
      const res = await fetch(`${BASE}/api/recheck-status?domain=${encodeURIComponent(domain)}`, {
        headers: headers(),
      });
      if (!res.ok) return { verified: [], pending: [], count: 0 };
      return (await res.json()) as RecheckStatusResponse;
    } catch {
      return { verified: [], pending: [], count: 0 };
    }
  },

  // ── agent runs (Phase 2) ──────────────────────────────────────────────────
  startAgentRun(
    req: BriefRequest & { idempotency_key?: string },
  ): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>("/api/agent/run", req);
  },
  async listAgentRuns(status = "staged"): Promise<{ runs: AgentRunSummary[] }> {
    const res = await fetch(`${BASE}/api/agent/runs?status=${encodeURIComponent(status)}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as { runs: AgentRunSummary[] };
  },
  async getAgentRun(runId: string): Promise<AgentRunDetail> {
    const res = await fetch(`${BASE}/api/agent/run/${encodeURIComponent(runId)}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as AgentRunDetail;
  },
  approveAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>(`/api/agent/run/${encodeURIComponent(runId)}/approve`, {});
  },
  rejectAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>(`/api/agent/run/${encodeURIComponent(runId)}/reject`, {});
  },
  cancelAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>(`/api/agent/run/${encodeURIComponent(runId)}/cancel`, {});
  },
  /** The approved run's drafts as launch-kit assets (409 until a human approves). */
  async agentRunAssets(runId: string): Promise<{ run_id: string; bundle: string; assets: BundleAsset[] }> {
    const res = await fetch(`${BASE}/api/agent/run/${encodeURIComponent(runId)}/assets`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as { run_id: string; bundle: string; assets: BundleAsset[] };
  },
  /** Live run updates via SSE (browser only). The same-origin proxy injects auth; EventSource
   *  can't set headers. Returns the EventSource so the caller can close() it on unmount. */
  streamAgentRun(runId: string, onMessage: (msg: AgentStreamMessage) => void): EventSource | null {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return null;
    const es = new EventSource(`${BASE}/api/agent/run/${encodeURIComponent(runId)}/stream`);
    es.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as AgentStreamMessage);
      } catch {
        /* ignore malformed frame */
      }
    };
    es.onerror = () => es.close();
    return es;
  },

  // ── gamification (Phase 3) ────────────────────────────────────────────────
  /** Companion state + awards for this browser session. Best-effort: empty on any failure. */
  async getGamification(domain?: string): Promise<GamificationView> {
    if (typeof window === "undefined") return { state: null, awards: [] };
    const sid = getSessionId();
    const q = `session_id=${encodeURIComponent(sid)}${domain ? `&domain=${encodeURIComponent(domain)}` : ""}`;
    try {
      const res = await fetch(`${BASE}/api/gamification?${q}`, { headers: headers() });
      if (!res.ok) return { state: null, awards: [] };
      return (await res.json()) as GamificationView;
    } catch {
      return { state: null, awards: [] };
    }
  },
  /** Recompute verified-win awards + score tiers (idempotent). `aeoScore` is the canonical
   *  number from lib/score.ts so the backend never invents one. Best-effort. */
  reconcileGamification(domain: string, aeoScore?: number): Promise<GamificationView["state"] extends never ? never : unknown> {
    return postJson("/api/gamification/reconcile", { session_id: getSessionId(), domain, aeo_score: aeoScore ?? null });
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

  /** Capture a user override of an LLM/system suggestion (Task 7) as an eval signal —
   *  the {suggested → chosen} pair, logged via the events stream so /api/eval/overrides
   *  can export it. Coexists with the R2-4 captureOverride proposal flow. Fire-and-forget;
   *  only call when the user actually changed the suggestion (the caller diffs). */
  trackOverride(
    field: string,
    suggested: unknown,
    chosen: unknown,
    opts?: { source?: string; reason?: string; run_id?: number | null; task_id?: string; rec_id?: number },
  ): void {
    this.track("user_override", {
      field,
      suggested: suggested ?? null,
      chosen: chosen ?? null,
      ...(opts ?? {}),
    });
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
