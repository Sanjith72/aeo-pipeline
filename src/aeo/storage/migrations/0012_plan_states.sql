-- B1 (retention foundation): a server home for the interactive plan so progress is no
-- longer trapped in one browser's localStorage. A plan_state is the durable, resumable
-- artifact behind the /plan/<id> link — it carries the rendered plan + a profile snapshot,
-- the canonical AEO score at issue, and the set of completed task ids.
--
-- Identity is an unguessable minted token (repos/plan_state.new_id), so the link works for
-- the no-website path too (which has no run_id) and is safe to share. The optional
-- session_id powers same-browser auto-resume; run_id ties back to a deep audit when one
-- exists (and seeds the score-over-time delta in a later spec).
--
-- Additive and idempotent — no existing table is altered. set_updated_at() is from 0001.

CREATE TABLE IF NOT EXISTS plan_states (
    id             TEXT         PRIMARY KEY,                 -- minted token, in the /plan/<id> URL
    session_id     TEXT,                                     -- cookie sid that created it (auto-resume)
    run_id         INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    business_name  TEXT,
    domain         TEXT,
    plan           JSONB        NOT NULL,                    -- the StructuredPlan the view renders
    profile        JSONB,                                    -- SiteProfile snapshot (overview + score inputs)
    score_snapshot INTEGER,                                  -- canonical AEO score at issue (seeds delta later)
    done_task_ids  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Auto-resume looks up the newest plan for a returning session.
CREATE INDEX IF NOT EXISTS idx_plan_states_session ON plan_states (session_id, updated_at DESC);

DROP TRIGGER IF EXISTS trg_plan_states_updated_at ON plan_states;
CREATE TRIGGER trg_plan_states_updated_at
    BEFORE UPDATE ON plan_states
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
