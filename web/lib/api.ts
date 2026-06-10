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
  deliverables(req: BriefRequest & { draft_limit?: number }): Promise<DeliverablesResponse> {
    return postJson<DeliverablesResponse>("/api/deliverables", req);
  },
  async deliverablesZip(req: BriefRequest & { draft_limit?: number }): Promise<Blob> {
    let res: Response;
    try {
      res = await fetch(`${BASE}/api/deliverables.zip`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify(req),
      });
    } catch {
      throw new Error(`Cannot reach the API at ${BASE}. Is it running?  (aeo serve)`);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`API ${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
    }
    return res.blob();
  },
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
  startAudit(req: { domain: string; name?: string }): Promise<{ job_id: string; status: string }> {
    return postJson<{ job_id: string; status: string }>("/api/audit", req);
  },
  async auditStatus(jobId: string): Promise<AuditJob> {
    const res = await fetch(`${BASE}/api/audit/${jobId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as AuditJob;
  },
  async siteReport(runId: number): Promise<SiteReportResponse> {
    const res = await fetch(`${BASE}/api/site-report/${runId}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as SiteReportResponse;
  },
};
