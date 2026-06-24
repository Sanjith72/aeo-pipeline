-- Feature #2 (predicted score lift): persist the per-recommendation PREDICTED
-- rubric-point lift AT ISSUE TIME, so the UI can show "+X pts" BEFORE the user
-- acts, and so a later re-crawl can hold the prediction accountable (predicted
-- vs actual) for calibration.
--
-- The lift is derived deterministically by the Validation loop's own simulate ->
-- re-score machinery (one rec applied at a time, cumulative-marginal in gap
-- priority order), so per-rec deltas never double-count a criterion's headroom.
-- Unit is rubric points (the page total's native 0-50 scale; all criterion
-- weights are 1.0, so a tier gain a->b is exactly b-a points).
--
--   predicted_delta  point estimate (NULL = the simulator could not estimate;
--                    render "—", never a fake 0)
--   predicted_low    tier-short conservative bound  = max(0, point - weight)
--   predicted_high   = point (the bounded-to-target, optimistic case)
--   predicted_basis  provenance:
--                      'simulated'             — a real positive estimate
--                      'no_deterministic_lift' — applied but the total did not move
--                                                (already at/above target / competitor pressure)
--                      'unknown'               — advisory-only rec, nothing to simulate
--
-- The matching ACTUAL side lives on recommendation_outcomes: detected_tier pins
-- the criterion's freshly re-scored tier when a re-crawl confirms the fix, so
-- actual_delta = (detected_tier - baseline_tier) * weight joins to the prediction
-- by rec_id.
--
-- Additive + idempotent; every column is nullable so pre-existing rows stay valid
-- (they simply carry no prediction until the recommendation is re-issued).

ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS predicted_delta NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_low   NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_high  NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_basis VARCHAR(30);

ALTER TABLE recommendation_outcomes
    ADD COLUMN IF NOT EXISTS detected_tier SMALLINT;
