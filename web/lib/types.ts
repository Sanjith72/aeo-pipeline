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

export interface DeliverablesResponse {
  manifest: { bundle: string; asset_count: number; assets: { path: string; kind: string }[] };
  assets: BundleAsset[];
}

export interface ProfileResponse {
  profile: SiteProfile;
  coverage: { pct: number; total_nodes: number; missing: number } & Record<string, unknown>;
  discovered: number;
  source: string;
}

export interface AuditJob {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress: string;
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
