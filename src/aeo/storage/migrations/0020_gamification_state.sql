-- Phase 3 gamification: derived per-session companion state. Reads verdicts from existing
-- tables (recommendation_outcomes, etc.); persists only derived state. Keyed on session_id
-- (the auth-free DAU identity, like events/plan_states). Additive + idempotent.

CREATE TABLE IF NOT EXISTS gamification_state (
    session_id        TEXT        PRIMARY KEY,
    client_id         INTEGER     REFERENCES clients(id) ON DELETE SET NULL,
    domain            TEXT,
    maturity_stage    VARCHAR(20) NOT NULL DEFAULT 'foundations'
        CHECK (maturity_stage IN ('foundations','on_radar','recommended','authority','cited_leader')),
    aeo_score         INTEGER,
    aeo_band          VARCHAR(20),
    momentum          INTEGER     NOT NULL DEFAULT 0,
    last_verified_at  TIMESTAMPTZ,
    verified_wins     INTEGER     NOT NULL DEFAULT 0,
    citations_earned  INTEGER     NOT NULL DEFAULT 0,
    track_progress    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gam_state_domain ON gamification_state (domain);

DROP TRIGGER IF EXISTS trg_gam_state_updated_at ON gamification_state;
CREATE TRIGGER trg_gam_state_updated_at
    BEFORE UPDATE ON gamification_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
