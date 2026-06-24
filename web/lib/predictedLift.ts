// Feature #2 — pure display helpers for the per-fix PREDICTED rubric-point lift. Kept here
// (lib, no JSX) so the honesty rules are unit-tested with `node --test`; results.tsx just
// renders what these return. The cardinal rule: when there is no honest positive estimate,
// show "—" — never a fabricated "+0".

import type { PredictedLift, VerifiedOutcome } from "./types";

export interface LiftChip {
  /** What the chip shows: "+3 pts" when known, "—" otherwise. */
  label: string;
  /** The tier-short band as a secondary hint (e.g. "+2 to +3"), or null. */
  band: string | null;
  /** True only for a real, positive, simulated estimate. */
  known: boolean;
}

const EM_DASH = "—";

/** The chip for one predicted lift. Known only when the simulator produced a real positive
 *  estimate (basis "simulated", point > 0). An advisory we couldn't estimate ("unknown") and
 *  an applied-but-no-movement fix ("no_deterministic_lift") both render "—" — honest, not 0. */
export function predictedLiftChip(p: PredictedLift | null | undefined): LiftChip {
  if (!p || p.point == null || p.point <= 0 || p.basis !== "simulated") {
    return { label: EM_DASH, band: null, known: false };
  }
  const point = Math.round(p.point);
  const lo = p.low == null ? null : Math.round(p.low);
  const hi = p.high == null ? null : Math.round(p.high);
  const band = lo != null && hi != null && lo < hi ? `+${lo} to +${hi}` : null;
  return { label: `+${point} pts`, band, known: true };
}

/** Predicted vs actual for a verified fix, e.g. "predicted +2 · actual +3" — keeps the
 *  estimate accountable once a re-crawl confirms the fix. Null when neither side is known. */
export function reconcileLabel(
  v: Pick<VerifiedOutcome, "predicted_delta" | "actual_delta">,
): string | null {
  const parts: string[] = [];
  if (v.predicted_delta != null) parts.push(`predicted +${Math.round(v.predicted_delta)}`);
  if (v.actual_delta != null) parts.push(`actual +${Math.round(v.actual_delta)}`);
  return parts.length ? parts.join(" · ") : null;
}
