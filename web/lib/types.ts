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
  // 0–1 expected impact on citations/visibility (aeo.report.packager._impact_score) — drives
  // coin-burst size + enemy scale in the gamified Map view (see lib/quest).
  impact_score: number;
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
  // Carried from build_plan (migration 0024) so the shared TaskHowTo expander renders the
  // same superset as the no-domain plan view: the "Where you are now" context line and the
  // optional "Doing it with AI" prompt. prompts is page-task-only (absent on vis:* tasks).
  current_state?: string;
  prompts?: { ai: string; human: string };
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
  // A one-line "about" blurb (Wikidata schema:description today; a crawl summary would win
  // if available). Null when neither source has one.
  about?: string | null;
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

// The async "personalize my downloadable files" job (#7): the in-app plan is instant
// (POST /api/deliverables, use_llm=false); this job upgrades the downloadable page drafts
// to AI-written prose without holding a request open. Polled via GET /api/deliverables/{id};
// `result` is the full DeliverablesResponse on success. Mirrors the AuditJob shape.
export interface DeliverablesJob {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress: string;
  stages: AuditStage[];
  result: DeliverablesResponse | null;
  error: string | null;
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
  // Only on source="unavailable" — why there are no suggestions. "llm_disabled": this
  // deployment has no AI configured (permanent until ops adds keys); "llm_failed":
  // providers errored/timed out (transient — retry-worthy); "no_results": the AI ran
  // fine and genuinely proposed nothing usable; "verification_failed": it proposed
  // candidates but live domain probes dropped them all (verify=true callers only).
  // NOTE: the `| string` widening means tsc can't catch a typo'd literal — the
  // reason→state mapping lives in lib/suggest.ts and is pinned by lib/suggest.test.ts.
  reason?: "llm_disabled" | "llm_failed" | "no_results" | "verification_failed" | string;
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

// Feature #2 — the deterministic predicted rubric-point lift for one fix, shown BEFORE
// the user acts. Mirrors aeo.validation.predict.PredictedLift. point/low/high are null
// when the simulator couldn't estimate (basis 'unknown') — render "—", never a fake 0.
export interface PredictedLift {
  point: number | null;
  low: number | null;
  high: number | null;
  unit: string; // "rubric_points"
  basis: string; // "simulated" | "no_deterministic_lift" | "unknown"
}

// A pending (not-yet-done) per-page fix with its predicted lift — the "pick high-impact
// work" side of Feature #2 (GET /api/recheck-status .pending[]).
export interface PendingFix {
  url: string;
  criterion: string | null;
  action_required: string;
  predicted: PredictedLift;
}

// GET /api/recheck-status?domain= — re-crawl-verified outcomes (Spec #2 "Verified live"),
// now carrying predicted vs actual rubric-point lift (Feature #2) so the estimate stays
// accountable once a fix is verified.
export interface VerifiedOutcome {
  url: string;
  criterion: string | null;
  detected_at: string | null;
  predicted_delta?: number | null;
  actual_delta?: number | null;
}

export interface RecheckStatusResponse {
  verified: VerifiedOutcome[];
  pending?: PendingFix[];
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

// ── agent runs (Phase 2: assistive copilot + human review) ───────────────────
export interface AgentStep {
  seq: number;
  agent: string;            // planner | research | builder | critic
  tool?: string | null;
  status: string;           // ok | failed | skipped
  model?: string | null;
  tokens?: number | null;
  cost_usd?: number | null;
  detail?: Record<string, unknown> | null;
}

export interface CriticVerdict {
  passed: boolean;
  independent_passed: boolean;
  claims_flagged: boolean;
  claims: string[];
  needs_review: boolean;
  reasons?: string[];       // compact "why flagged" summary
}

export interface AgentTask {
  id: string;
  title: string;
  slug?: string;
  status: string;           // proposed | drafted | reviewed | flagged
  draft?: { body_markdown?: string; draft_quality?: string; word_count?: number } | null;
  critic?: CriticVerdict | null;
}

export interface AgentRunSummary {
  id: string;
  status: string;           // queued | planning | staged | approved | rejected | failed | cancelled
  domain?: string | null;
  current_step?: string | null;
  updated_at?: string | null;
}

export interface AgentRunDetail extends AgentRunSummary {
  result?: { domain?: string; topic?: string; headline?: string; tasks?: AgentTask[] } | null;
  steps?: AgentStep[];
  error?: string | null;    // set when status is "failed"
}

export type AgentStreamMessage =
  | { type: "step"; step: AgentStep }
  | { type: "status"; status: string; current_step?: string | null }
  | { type: "done"; status: string; result?: AgentRunDetail["result"] }
  | { type: "error"; detail: string };

// ── gamification (Phase 3: honest, verified-outcome rewards) ─────────────────
export interface GamificationState {
  session_id: string;
  domain?: string | null;
  maturity_stage: string;
  aeo_score?: number | null;
  aeo_band?: string | null;
  momentum: number;
  verified_wins: number;
  citations_earned: number;
}

export interface GamificationAward {
  id: number;
  award_type: string;     // verified_win | citation | status_tier | maturity_up
  criterion?: string | null;
  score_delta?: number | null;
  created_at?: string | null;
}

export interface GamificationView {
  state: GamificationState | null;
  awards: GamificationAward[];
}

// ── v5 contracts (docs/V5_CONTRACTS.md) — 5-skill scoring, packs, free overview,
// tickets, entitlements. The frontend half of the CH-13 lock.

export type SkillKey =
  | "messaging"
  | "conversion"
  | "discovery_visibility"
  | "proof_trust"
  | "structure_ux";

export interface SkillSuggestion {
  id: string;
  text: string;
  // The rubric criterion behind the suggestion; null for the P1 heuristic
  // Messaging/Conversion signals (their LLM criteria land in P2).
  criterion: string | null;
}

export interface SkillScore {
  score: number; // 0-100
  // "hybrid" = an LLM refined a source criterion; "provisional" = P1 heuristic
  // (Messaging/Conversion); "neutral" = honestly couldn't judge — never a fake 0.
  confidence: "deterministic" | "hybrid" | "provisional" | "neutral" | string;
  source_criteria: string[];
  suggestions: SkillSuggestion[];
  evidence?: Record<string, unknown>;
}

// One impact-ranked fix in the "do these first" list (CH-06): ranked by weight × severity
// across every skill, so high-weight failures surface above low-weight passes.
export interface SkillPriority {
  skill: SkillKey;
  text: string;
  criterion: string | null;
  skill_score: number;
  impact: number;
}

export interface SkillScores {
  skills_version: string;
  overall: number; // 0-100, weighted by per-skill weights (CH-06)
  skills: Record<SkillKey, SkillScore>;
  priorities: SkillPriority[];
}

export interface PackPage {
  url: string;
  page_type: string;
  final_score: number;
  rank: number;
}

export interface PackPreview {
  pack_index: number; // 1 = homepage pack, always
  title: string;
  impact_score: number;
  page_count: number;
  pages: PackPage[];
  locked: boolean; // entitlement-derived; anonymous = Pack 1 unlocked, rest locked
  status: "preview" | "unlocked" | "crawled" | "scored" | string;
}

// GET /api/packs/{run_id} — the packs persisted for a deep-audit run (CH-03). Same
// PackPreview shape as the overview's live preview, so one card renders both.
export interface PacksResponse {
  run_id: number;
  packs: PackPreview[];
}

// GET /api/packs/{run_id}/{pack_index} — the gated deep value (CH-02a): per-page five-skill
// detail. 403 when the pack is locked for the caller (enforced server-side).
export interface PackPageDetail {
  url: string;
  page_type: string;
  overall: number | null;
  detail: { skills: Record<SkillKey, SkillScore>; priorities: SkillPriority[] } | null;
}

export interface PackDetailResponse {
  run_id: number;
  pack_index: number;
  title: string;
  pages: PackPageDetail[];
}

// v5 CH-14 — the AI-snapshot: does the page get cited by AI answer engines (Perplexity)?
// "unavailable" = the check didn't run (engine unconfigured / no question / timed out) —
// never conflated with "not_cited". `via` says whether a match was a structured citation
// (hard) or an answer-text mention (softer).
export interface AiVisibility {
  status: "cited" | "not_cited" | "unavailable" | string;
  engine: string;
  question?: string | null;
  reason?: string | null; // only on status="unavailable"
  via?: "citations" | "answer_text" | null;
  matched?: string[];
  cached?: boolean;
}

// POST /api/overview — the free URL-first entry (CH-09). No auth, cached per domain.
export interface OverviewResponse {
  domain: string;
  route: "rich" | "thin" | "dead" | string;
  cached: boolean;
  generated_at: string;
  site: {
    industry: string | null;
    industry_source: string | null;
    location: string | null;
    services: string[];
    about: string | null;
    cms_type: string | null;
    discovered: number;
    source: string | null;
  };
  coverage: {
    pct: number | null;
    matched: number | null;
    total_nodes: number | null;
    missing: number | null;
    top_missing: { slug: string | null; title?: string | null; priority?: number | null }[];
  } | null;
  homepage: { url: string; aeo_total: number; aeo_max: number; priority_tier: string } | null;
  skills: SkillScores | null;
  skills_unavailable_reason?: string | null;
  ai_visibility?: AiVisibility | null;
  packs: PackPreview[];
  competitors: { names: string[]; reason: string | null };
  next: { deeper: string };
}

// Ticket contract (CH-08) — P0 lock; the board UI + close-triggered verification land in
// P5. MilestoneStatus stays 3-state until then (StatusControl/STATUS_META/useQuestTracker
// hardcode it and are migrated together).
export type TicketStatus = "pending" | "in_progress" | "closed_pending_verify" | "verified_completed";

export interface TicketFields {
  assignee: string | null;
  target_date: string | null; // ISO date
  page_url: string | null;
  skill: SkillKey | null;
  baseline_score: number | null; // 0-100 skill score pinned at ticket open
  current_score: number | null; // refreshed by the verification re-crawl
  closed_at: string | null;
}

// A v5 ticket (CH-08/CH-15) — one per (page, skill), grouped one milestone per pack.
// Distinct from the agency MilestoneTask; carries the 4-state TicketStatus + before/after.
export interface Ticket extends TicketFields {
  id: number;
  task_key: string;
  label: string;
  action_required: string | null;
  how_to: string | null;
  status: TicketStatus;
  status_source: "manual" | "crawl";
  detected_at: string | null;
  pack_index: number;
}

export interface TicketsResponse {
  run_id: number;
  pack_index?: number;
  tickets: Ticket[];
}

// Entitlements (CH-02b) — P0 lock; enforcement arrives with auth in P4. Payments are
// stubbed by decision (§9.2): grants come from source "manual" | "promo" until a
// provider is chosen.
export interface Entitlement {
  user_id: string;
  domain: string;
  scope: "free_overview" | "pack" | "all_packs" | "tickets";
  pack_index: number | null; // null unless scope === "pack"
  expires_at: string | null;
  source: "manual" | "stripe" | "promo" | string;
}
