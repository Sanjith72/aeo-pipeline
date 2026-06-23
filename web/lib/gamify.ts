// web/lib/gamify.ts
// Pure maturity-ladder helpers for the gamification UI — no I/O, unit-testable.

export const MATURITY_ORDER = ["foundations", "on_radar", "recommended", "authority", "cited_leader"] as const;
export type MaturityStage = (typeof MATURITY_ORDER)[number];

export const MATURITY_LABEL: Record<MaturityStage, string> = {
  foundations: "Foundations",
  on_radar: "On the radar",
  recommended: "Recommended",
  authority: "Authority",
  cited_leader: "Cited Leader",
};

/** 0–1 position of a stage on the ladder, for a progress bar. */
export function maturityProgress(stage: MaturityStage): number {
  const i = MATURITY_ORDER.indexOf(stage);
  return i < 0 ? 0 : i / (MATURITY_ORDER.length - 1);
}
