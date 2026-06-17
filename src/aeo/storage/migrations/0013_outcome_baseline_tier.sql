-- Slice B (verified re-crawl moat): pin the TARGETED criterion's score tier at issue
-- time, so a re-crawl can confirm the SPECIFIC fix landed (the criterion's tier rose),
-- not merely that the page changed. The hash-only signal this replaces was the
-- self-grading the v4 validator's no-self-grading rule exists to avoid.
--
-- Additive + idempotent; nullable so pre-existing outcomes stay valid — they simply
-- can't be criterion-verified and remain pending until the recommendation is re-issued
-- (which opens a fresh outcome with a pinned baseline_tier).

ALTER TABLE recommendation_outcomes
    ADD COLUMN IF NOT EXISTS baseline_tier INTEGER;
