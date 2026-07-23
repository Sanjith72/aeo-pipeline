-- v5 P4 identity bridge (CH-07): stamp milestone ownership so P5 can flip per-user
-- enforcement on without another migration + backfill. Additive ONLY — GET /api/milestones
-- stays domain-keyed/anonymous in P4 (gating it now would break the shipped dashboard).
-- implementation_milestones already self-enabled RLS at 0026, so no new ENABLE line here;
-- the backend connects as table owner and owners bypass non-FORCE RLS (app-level checks).

ALTER TABLE implementation_milestones
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES app_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_milestones_owner_user
    ON implementation_milestones (owner_user_id) WHERE owner_user_id IS NOT NULL;
