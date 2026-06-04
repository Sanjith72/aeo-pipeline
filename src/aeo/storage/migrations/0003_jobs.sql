-- DB-backed job queue. Single-row claim via FOR UPDATE SKIP LOCKED.
-- Scales to thousands of jobs / second on a single PG instance.

CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL      PRIMARY KEY,
    kind            VARCHAR(40)    NOT NULL,         -- e.g. 'crawl_batch'
    payload         JSONB          NOT NULL,
    status          VARCHAR(20)    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','succeeded','failed','dead')),
    attempts        INTEGER        NOT NULL DEFAULT 0,
    max_attempts    INTEGER        NOT NULL DEFAULT 4,
    run_after       TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    locked_by       VARCHAR(120),
    locked_at       TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_ready
    ON jobs (run_after)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs (kind);
