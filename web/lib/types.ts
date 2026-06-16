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

export interface DeliverablesResponse {
  manifest: { bundle: string; asset_count: number; assets: { path: string; kind: string }[] };
  plan?: StructuredPlan; // #10 — the interactive in-app plan
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
  source: "llm" | "unavailable" | string;
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
