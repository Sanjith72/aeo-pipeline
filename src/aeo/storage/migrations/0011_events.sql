-- Instrumentation (Block F): a single append-only product-analytics log so we can
-- actually measure whether the retention loop works (Arun's metric is DAU). Kept in
-- Postgres, consistent with the all-in-DB architecture — no third-party analytics.
--
-- Additive and idempotent — no existing table is altered.
--
-- Identity note: `session_id` is a stable id the browser mints once and persists in a
-- cookie (see web/lib/api.ts), so DAU / return-rate can be computed without auth. The
-- optional `client_id` ties an event to a known client row when one exists (the
-- implementation-rate metric instead joins recommendation_outcomes from 0010).

CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL    PRIMARY KEY,
    -- Browser-minted, cookie-persisted. The unit of DAU / return-rate.
    session_id  TEXT         NOT NULL,
    -- Known client when one exists; dangling-safe if the client is ever pruned.
    client_id   INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    event_type  TEXT         NOT NULL,   -- 'session_start' | 'wizard_step_completed' | 'plan_viewed' | 'task_marked_done' | 'return_visit' | …
    url         TEXT,                    -- the audited/site URL the event is about, when relevant
    metadata    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- DAU and per-type rollups scan by (type, time).
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events (event_type, created_at);
-- Return-rate groups by session.
CREATE INDEX IF NOT EXISTS idx_events_session ON events (session_id);
