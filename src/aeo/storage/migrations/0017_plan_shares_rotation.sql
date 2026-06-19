-- Revoke & Regenerate — let a client accumulate REVOKED share rows (an audit trail of
-- killed links) while still guaranteeing exactly ONE active token at a time.
--
-- 0013 declared `client_id` as a column-level UNIQUE, which allowed only a single row per
-- client — fine for mint-once, but it blocks rotation: revoke-and-regenerate must keep the
-- old row (with revoked_at set) alongside the new active one. Replace the total uniqueness
-- with a PARTIAL unique index scoped to active (non-revoked) rows.
--
-- Additive + idempotent.

ALTER TABLE plan_shares DROP CONSTRAINT IF EXISTS plan_shares_client_id_key;

-- At most one ACTIVE (revoked_at IS NULL) token per client; revoked rows are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_shares_active_client
    ON plan_shares (client_id) WHERE revoked_at IS NULL;
