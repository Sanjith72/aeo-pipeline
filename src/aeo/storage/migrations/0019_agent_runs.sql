-- src/aeo/storage/migrations/0019_agent_runs.sql
-- Phase 2A: durable, resumable agent runs on the existing Postgres job queue (no new broker).
-- agent_runs is the run-state spine; agent_steps is the per-step trace + resume log. Every run
-- ends 'staged' and only a human approve/reject advances it (assistive-copilot + human gate).
-- Additive and idempotent. set_updated_at() is from 0001; clients(id) is from 0001.

CREATE TABLE IF NOT EXISTS agent_runs (
    id               TEXT         PRIMARY KEY,              -- minted token (repos/agent_runs.new_id)
    idempotency_key  TEXT UNIQUE,                            -- collapse duplicate enqueues (NULL = no dedupe)
    domain           TEXT,
    client_id        INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','planning','staged','approved','rejected','failed','cancelled')),
    current_step     VARCHAR(40),
    brief            JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- the BusinessInput that seeded the run
    result           JSONB,                                      -- the staged task graph (Planner output)
    error            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_domain ON agent_runs (domain);

DROP TRIGGER IF EXISTS trg_agent_runs_updated_at ON agent_runs;
CREATE TRIGGER trg_agent_runs_updated_at
    BEFORE UPDATE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS agent_steps (
    id          BIGSERIAL    PRIMARY KEY,
    run_id      TEXT         NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq         INTEGER      NOT NULL,
    agent       VARCHAR(40)  NOT NULL,                       -- 'planner' | 'research' | 'builder' | 'critic'
    tool        VARCHAR(80),                                 -- the wrapped engine seam, e.g. 'plan_from_brief'
    status      VARCHAR(20)  NOT NULL DEFAULT 'ok'
        CHECK (status IN ('ok','failed','skipped')),
    model       VARCHAR(80),                                 -- LLM model used (NULL for deterministic steps)
    tokens      INTEGER,
    cost_usd    NUMERIC(10,6),                               -- per-step frontier cost (Phase 2B fills this)
    latency_ms  INTEGER,
    error_class VARCHAR(40),                                 -- exception type for failure triage
    detail      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps (run_id, seq);
