-- Observability: one row per agent step per page, written by every pipeline
-- stage (Analyze → Gap → Recommend → Validate → Report) and the Error Sink.
-- Powers `aeo trace <page>` (dump a page's journey, ordered by time).
-- run_id / page_id are nullable so run-level or pre-crawl steps can still trace.

CREATE TABLE IF NOT EXISTS agent_traces (
    id              BIGSERIAL      PRIMARY KEY,
    run_id          INTEGER        REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    page_id         BIGINT         REFERENCES crawled_pages(id) ON DELETE CASCADE,
    agent           VARCHAR(40)    NOT NULL,        -- analyzer|gap|recommender|validator|reporter|prioritizer
    step            VARCHAR(60),                    -- finer-grained label within an agent
    status          VARCHAR(20)    NOT NULL
                        CHECK (status IN ('started','success','failed','skipped')),
    duration_ms     INTEGER,
    model           VARCHAR(100),                   -- LLM model used (NULL for deterministic steps)
    tokens          INTEGER,                        -- prompt+completion tokens, when known
    error           TEXT,                           -- populated by the Error Sink on failure
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_traces_page  ON agent_traces (page_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_run   ON agent_traces (run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_agent ON agent_traces (agent);
