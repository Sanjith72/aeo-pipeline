// Pre-select "Your goals" (step 3) from what the crawl/profile already told us, so the
// owner edits a sensible starting set instead of a blank slate. This is a SUGGESTION, not
// a lock: the wizard pre-checks these and the user can toggle any of them.
//
// The mapping is an explicit, documented function of signals the /api/profile (and the
// richer deep-audit) SiteProfile already carries — no new API surface. When the profile
// gives no clear signal we return nothing and leave the step empty rather than guessing.

import type { SiteProfile } from "./types";

// The exact GOAL_OPTIONS labels we map onto, kept in lockstep with web/lib/options.ts.
// goals.test.ts asserts every one of these still exists in GOAL_OPTIONS so a label rename
// can't silently break the mapping.
export const GOAL_AI_ANSWERS = "Show up in AI answers";
export const GOAL_WIN_CUSTOMERS = "Win more customers";
export const GOAL_BEAT_COMPETITORS = "Beat my competitors";
export const GOAL_LOCAL = "Grow local business";
export const GOAL_AUTHORITY = "Build my brand's authority";
export const GOAL_SELL_ONLINE = "Sell more online";

// Canonical output order (matches GOAL_OPTIONS), so the pre-checked set is stable.
const GOAL_ORDER = [
  GOAL_AI_ANSWERS,
  GOAL_WIN_CUSTOMERS,
  GOAL_BEAT_COMPETITORS,
  GOAL_LOCAL,
  GOAL_AUTHORITY,
  GOAL_SELL_ONLINE,
] as const;

export interface GoalSignals {
  /** The fast or deep SiteProfile. Null on a dead/no-site route → structural signals are
   *  skipped (only the industry/competitor signals below can still fire). */
  profile: SiteProfile | null;
  /** Resolved specific industry (ProfileResponse.industry), e.g. "E-commerce & Retail". */
  industry?: string | null;
  /** Ideal-page coverage %, 0–100 (ProfileResponse.coverage.pct). Lower = thinner site. */
  coveragePct?: number | null;
  /** How many competitors we already surfaced (on-site + picker) — a benchmark signal. */
  competitorCount?: number;
}

// Buyer-journey gap names that mean "visitors don't convert" (the decision/action end).
const _CONVERSION_GAPS = new Set(["decision", "conversion", "action", "purchase", "retention"]);
// business_intent.model / industry fragments that mean online selling.
const _COMMERCE_RE = /ecommerce|e-commerce|retail|shop|store|marketplace|product/i;
// …and ones that mean a place-based / serve-a-local-area business.
const _LOCAL_RE = /local|service|clinic|dental|practice|restaurant|salon|contractor|agency/i;

/**
 * Map crawl/profile signals to a set of pre-checked GOAL_OPTIONS labels.
 *
 * The documented rules (each fires independently; a goal is suggested if ANY of its rules
 * match). Every rule reads a field already on the response — nothing is invented:
 *
 *   • Show up in AI answers  ← low structured-data foundation (structure_score < 0.6),
 *     OR weak answer-readiness across the buyer journey (covered/total stages < 0.6),
 *     OR thin ideal-page coverage (coveragePct < 60). These are exactly the schema /
 *     Q&A / entity-visibility gaps that decide whether AI assistants can cite the site.
 *   • Build my brand's authority ← thin content: very few pages (page_count ≤ 3) or
 *     fewer than half the ideal page archetypes present.
 *   • Win more customers     ← a decision/conversion-stage gap (visits don't convert).
 *   • Beat my competitors    ← we already surfaced ≥ 1 competitor to benchmark against.
 *   • Sell more online       ← an e-commerce/retail business model or industry.
 *   • Grow local business    ← a local/place-based service business model.
 *
 * Returns the matching labels in canonical GOAL_OPTIONS order, deduped. An empty array
 * (no clear signal) means "leave the step unchecked".
 */
export function goalsFromProfile(sig: GoalSignals): string[] {
  const picked = new Set<string>();
  const p = sig.profile;

  if (p) {
    const c = p.classification;
    const stages = p.journey?.stages ?? [];
    const coveredStages = stages.filter((s) => s.covered).length;
    const present = c?.present_archetypes?.length ?? 0;
    const missing = c?.missing_archetypes?.length ?? 0;
    const archetypeTotal = present + missing;
    const model = (p.business_intent?.model ?? "").toLowerCase();

    const lowFoundation = typeof c?.structure_score === "number" && c.structure_score < 0.6;
    const weakAnswerReadiness = stages.length > 0 && coveredStages / stages.length < 0.6;
    const thinCoverage = typeof sig.coveragePct === "number" && sig.coveragePct < 60;
    if (lowFoundation || weakAnswerReadiness || thinCoverage) picked.add(GOAL_AI_ANSWERS);

    const thinContent =
      (typeof c?.page_count === "number" && c.page_count <= 3) ||
      (archetypeTotal > 0 && present / archetypeTotal < 0.5);
    if (thinContent) picked.add(GOAL_AUTHORITY);

    const conversionGap = (p.journey?.gaps ?? []).some((g) => _CONVERSION_GAPS.has(g.toLowerCase()));
    if (conversionGap) picked.add(GOAL_WIN_CUSTOMERS);

    if (_COMMERCE_RE.test(model)) picked.add(GOAL_SELL_ONLINE);
    if (_LOCAL_RE.test(model)) picked.add(GOAL_LOCAL);
  }

  // Industry- and competitor-derived signals fire even on a thin/None profile.
  const industry = (sig.industry ?? "").toLowerCase();
  if (_COMMERCE_RE.test(industry)) picked.add(GOAL_SELL_ONLINE);
  if ((sig.competitorCount ?? 0) > 0) picked.add(GOAL_BEAT_COMPETITORS);

  return GOAL_ORDER.filter((g) => picked.has(g));
}
