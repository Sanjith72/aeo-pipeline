// TypeScript mirrors of the SP-4a API payloads (see PRODUCT_FLOW.md §3 and
// aeo.intelligence.site_profile.SiteProfile.to_dict / aeo.intelligence.brief.BriefPlan).

export interface StrategyAction {
  priority: number;
  title: string;
  detail: string;
  category: string;
  effort: string;
  related_slugs: string[];
}

export interface JourneyStage {
  stage: string;
  present_count: number;
  covered: boolean;
  examples: string[];
}

export interface SiteProfile {
  domain: string;
  scenario: string;
  deliverable: string;
  headline: string;
  narrative: string;
  agency_mode: boolean;
  classification: {
    site_class: string;
    page_count: number;
    structure_score: number;
    type_distribution: Record<string, number>;
    present_archetypes: string[];
    missing_archetypes: string[];
  };
  business_intent: {
    model: string;
    confidence: number;
    decided_by: string;
    evidence: string[];
    scores: Record<string, number>;
  };
  journey: {
    stages: JourneyStage[];
    gaps: string[];
    filling_nodes: Record<string, string[]>;
  };
  actions: StrategyAction[];
}

export interface SitemapNode {
  slug: string;
  title: string;
  page_type: string;
  intent: string;
  journey_stage: string;
  cluster: string | null;
  priority: number;
  required_entities: string[];
  seed_questions: string[];
}

export interface BriefPlan {
  business: { name: string; key: string } & Record<string, unknown>;
  blueprint: {
    topic: string;
    version: number;
    generator: string;
    ideal_pages: number;
    sitemap: SitemapNode[];
  };
  coverage: { pct: number; total_nodes: number; missing: number };
  profile: SiteProfile;
}

export interface BundleAsset {
  path: string;
  kind: string;
  content: string;
}

export interface ChecklistTask {
  id: string;
  label: string;
  detail?: string;
}

export interface ChecklistWeek {
  title: string;
  blurb: string;
  tasks: ChecklistTask[];
}

export interface PlanChecklist {
  weeks: ChecklistWeek[];
  total: number;
}

// The structured, phased plan (#8/#9/#4/#10) from aeo.report.packager.build_plan.
// Page tasks carry implementation prompts; visibility tasks (vis:*) carry none.
export interface PlanTask {
  id: string;
  label: string;
  detail?: string;
  phase: "week_1" | "week_2_4" | "later";
  quick_win: boolean;
  effort: "low" | "medium" | "high";
  priority?: number;
  current_state: string;
  action_required: string;
  how_to: string;
  prompts?: { ai: string; human: string };
}

export interface PlanPhase {
  key: "week_1" | "week_2_4" | "later";
  title: string;
  blurb: string;
  tasks: PlanTask[];
}

export interface StructuredPlan {
  phases: PlanPhase[];
  quick_win_ids: string[];
  quick_win_count: number;
  total: number;
}

// ── Implementation Milestones (persisted, per-site, auto-verified) ──────────────
// The "Final Plan" turned into trackable state: aeo.storage.repos.milestones +
// /api/milestones. Status advances either by the owner (manual) or by the weekly
// verification crawl (crawl) detecting the recommended artifact live on the site.
export type MilestoneStatus = "pending" | "in_progress" | "verified_completed";

export interface MilestoneTask {
  id: number;
  task_key: string;
  label: string;
  action_required: string;
  how_to: string;
  verify_kind: "page" | "service" | "heading" | "manual";
  verify_target: string | null;
  status: MilestoneStatus;
  status_source: "manual" | "crawl";
  detected_at: string | null;
  // Developer Handoff: a developer-ready technical brief (server-generated from the
  // verify signal — JSON-LD snippet, exact heading tag, etc.). Present on owner + share views.
  dev_brief?: string;
  // "I'll do it myself": the strictly paste-able artifact (JSON-LD <script> or bare heading
  // tag), or null for off-site tasks; plus the CMS-aware, numbered walkthrough.
  raw_snippet?: string | null;
  diy_steps?: string[];
}

export interface Milestone {
  milestone_key: string;
  title: string;
  blurb: string;
  status: MilestoneStatus;
  position: number;
  tasks: MilestoneTask[];
}

export interface MilestoneProgress {
  total: number;
  verified: number;
  in_progress: number;
  pct: number;
}

export interface MilestoneDashboard {
  milestones: Milestone[];
  progress: MilestoneProgress;
  // The client's stable read-only share token (owner views only). The UI builds the
  // Developer Handoff link as `${origin}/share/${share_token}`.
  share_token?: string;
}

export interface MilestoneVerifyResult {
  summary: { checked: number; newly_verified: number; verified_keys: string[] };
  dashboard: MilestoneDashboard;
}

// The public, read-only payload behind /share/[token] (GET /api/share/{token}). No
// share_token is echoed back — the viewer already holds it.
export interface SharedPlanResponse extends MilestoneDashboard {
  business_name: string;
  domain: string;
}

// R2-5 — the plan's tasks clustered by difficulty/maturity grade (the Strategy tab).
export interface StrategyGroup {
  grade: "foundation" | "growth" | "advanced" | string;
  title: string;
  difficulty: string;
  readme: { what: string; why: string; how: string };
  task_ids: string[];
  tasks: PlanTask[];
}

export interface StrategyView {
  groups: StrategyGroup[];
  grades: string[];
  total: number;
}

export interface DeliverablesResponse {
  manifest: { bundle: string; asset_count: number; assets: { path: string; kind: string }[] };
  plan?: StructuredPlan; // #10 — the interactive in-app plan
  strategy?: StrategyView; // R2-5 — tasks clustered by difficulty/maturity
  checklist?: PlanChecklist; // legacy flat-weeks list (zip fallback / back-compat)
  assets: BundleAsset[];
}

// /api/profile now branches on crawl quality (Block B #2/#3): a rich/thin site returns
// its profile + crawl-derived industry/location for the wizard to prefill; a dead crawl
// returns route='dead' (+ next) pointing at the no-website brief path.
export interface ProfileResponse {
  route: "rich" | "thin" | "dead" | string;
  profile: SiteProfile | null;
  industry: string | null;
  location: string | null;
  coverage?: { pct: number; total_nodes: number; missing: number } & Record<string, unknown>;
  discovered: number;
  source: string;
  next?: string;
  // Crawl-derived "About you" prefills: what the business offers + best-effort on-site
  // competitor signals (the wizard seeds these so the user edits instead of typing).
  services?: string[];
  competitors?: CompetitorSuggestion[];
  // Detected publishing platform ('wordpress' | 'shopify' | 'unknown'). Threaded into the
  // milestone sync so the dashboard's "I'll do it myself" steps match the platform.
  cms_type?: string | null;
  // Where the resolved specific industry came from: "wikidata" | "crawl" | "model".
  industry_source?: string | null;
  // R2-2 cache age: when this domain's homepage was last crawled, so the UI can show
  // "data from N hours ago" and offer an explicit re-crawl. Null = never crawled / no DB.
  last_crawled_at?: string | null;
  cache_age_hours?: number | null;
}

// One per-stage progress event the deep audit streams (#7) — see orchestrator.RUN_STAGES.
export interface AuditStage {
  stage: string;
  counts: Record<string, number | string | null>;
  at: number;
}

export interface AuditJob {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress: string;
  stages: AuditStage[];
  result: { run?: { run_id?: number }; analysis?: Record<string, number>; site_report_id?: number } | null;
  error: string | null;
  // R2-2 drop-off safety: true once the client has asked to cancel this run.
  cancelled?: boolean;
}

export interface SiteReportResponse {
  run_id: number;
  summary: string;
  sections: { strategy?: SiteProfile } & Record<string, unknown>;
}

export interface CompetitorSuggestion {
  name: string;
  domain: string;
}

export interface CompetitorSuggestResponse {
  competitors: CompetitorSuggestion[];
  source: "llm" | "onsite" | "unavailable" | string;
  // True when the strict industry+location ask was empty and broader peers were returned.
  relaxed?: boolean;
}

/** A competitor the user picked or typed — names are enough, URLs are optional. */
export interface CompetitorPick {
  name: string;
  domain?: string;
  source: "suggested" | "manual";
}

export interface BriefRequest {
  name: string;
  domain?: string;
  category?: string;
  topic?: string;
  location?: string;
  services: string[];
  competitors: string[];
  goals: string[];
  use_llm: boolean;
}

// ── retention foundation (Specs #1–#2) ──────────────────────────────────────────

// A persisted, resumable plan (B1) — the durable artifact behind the /plan/<id> link.
// Mirrors src/aeo/storage/repos/plan_state.py rows.
export interface PlanStateResponse {
  id: string;
  run_id: number | null;
  business_name: string | null;
  domain: string | null;
  plan: StructuredPlan;
  profile: SiteProfile | null;
  score_snapshot: number | null;
  done_task_ids: string[];
  created_at: string;
  updated_at: string;
}

// GET /api/plan-state?session_id= — what the homepage 'resume your plan' banner reads.
export interface ResumeResponse {
  id: string | null;
  business_name?: string | null;
  domain?: string | null;
}

// GET /api/recheck-status?domain= — re-crawl-verified outcomes (Spec #2 "Verified live").
export interface VerifiedOutcome {
  url: string;
  criterion: string | null;
  detected_at: string | null;
}

export interface RecheckStatusResponse {
  verified: VerifiedOutcome[];
  count: number;
}

// GET /api/site-freshness?domain= — has this domain been audited recently? (Slice 2b)
export interface SiteFreshnessResponse {
  fresh: boolean;
  run_id?: number;
  last_crawled_at?: string;
  status?: string;
  has_report?: boolean;
}
