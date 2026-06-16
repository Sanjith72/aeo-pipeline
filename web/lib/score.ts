// The canonical AEO Score (B0). One pure function over SiteProfile, reused on every
// surface (the results ring, the resumed plan, later the public scorecard) so the same
// site never shows two different numbers. Every input is already on SiteProfile; the
// rubric avg_pct from deep-audit pages is deliberately NOT mixed in — it's a different
// scale and would produce "two numbers for one site". The same formula runs on the fast
// profile and on the richer post-audit profile, so the number refines as the profile
// improves rather than jumping between scales.

import type { SiteProfile } from "./types";

// Weights live in one place so the formula is tunable without hunting through callers.
export const SCORE_WEIGHTS = {
  foundation: 0.4, // structure_score — the pages that matter exist and are sound
  journey: 0.35, // share of the buyer-journey stages the site covers
  presence: 0.15, // share of the ideal page archetypes already present
  confidence: 0.1, // how confidently we read the business model
} as const;

type ComponentKey = keyof typeof SCORE_WEIGHTS;
type Components = Partial<Record<ComponentKey, number>>;

function clamp01(n: number): number {
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
}

// Drop any component whose signal is absent (a 0 denominator → undefined) and renormalize
// the remaining weights, so a thin profile is never penalized for data we simply don't
// have. Returns a 0–100 integer; 0 when nothing is measurable.
function compose(c: Components): number {
  let weighted = 0;
  let totalWeight = 0;
  for (const key of Object.keys(SCORE_WEIGHTS) as ComponentKey[]) {
    const v = c[key];
    if (v === undefined) continue;
    weighted += SCORE_WEIGHTS[key] * clamp01(v);
    totalWeight += SCORE_WEIGHTS[key];
  }
  return totalWeight === 0 ? 0 : Math.round((weighted / totalWeight) * 100);
}

function components(profile: SiteProfile): Components {
  const c = profile.classification;
  const stages = profile.journey?.stages ?? [];
  const covered = stages.filter((s) => s.covered).length;
  const present = c?.present_archetypes?.length ?? 0;
  const missing = c?.missing_archetypes?.length ?? 0;
  const archetypeTotal = present + missing;
  return {
    foundation: typeof c?.structure_score === "number" ? c.structure_score : undefined,
    journey: stages.length ? covered / stages.length : undefined,
    presence: archetypeTotal ? present / archetypeTotal : undefined,
    confidence:
      typeof profile.business_intent?.confidence === "number" ? profile.business_intent.confidence : undefined,
  };
}

/** The site's AEO Score today, 0–100. */
export function aeoScore(profile: SiteProfile): number {
  return compose(components(profile));
}

/** Where the score reaches once the plan is done: every journey stage covered and every
 *  ideal page present, on a sound foundation. Confidence is left as-is — it's how well we
 *  understand the business, not something the plan changes. The ghosted target arc on the
 *  ring; never below the current score. */
export function aeoScoreCeiling(profile: SiteProfile): number {
  const base = components(profile);
  const ceiling = compose({
    foundation: base.foundation === undefined ? undefined : 1,
    journey: base.journey === undefined ? undefined : 1,
    presence: base.presence === undefined ? undefined : 1,
    confidence: base.confidence,
  });
  return Math.max(ceiling, aeoScore(profile));
}

export type ScoreTone = "rose" | "amber" | "sky" | "emerald";
export interface ScoreBand {
  label: string;
  verdict: string;
  tone: ScoreTone;
}

/** The four bands a score falls into — verdict copy + a tone for the ring. The labels
 *  become the named level tiers in a later spec (B4-full). */
export function scoreBand(score: number): ScoreBand {
  if (score < 40)
    return { label: "Barely visible", verdict: "Most AI assistants would skip you today.", tone: "rose" };
  if (score < 60)
    return { label: "On the radar", verdict: "You show up sometimes — but you're not the answer yet.", tone: "amber" };
  if (score < 80)
    return { label: "Recommended", verdict: "You're a likely recommendation. Close the gaps to lead.", tone: "sky" };
  return { label: "Top answer", verdict: "You're set up to be the business AI names first.", tone: "emerald" };
}
