-- Page Prioritization (Crawler block). Ranking is computed PRE-crawl from
-- discovered URLs, so rows reference the run + url, not a crawled_pages.id.
-- The per-page pipeline processes the top-N where selected = TRUE; the full
-- ranking is persisted for observability ("why was this page skipped?").

CREATE TABLE IF NOT EXISTS page_priorities (
    id              BIGSERIAL      PRIMARY KEY,
    run_id          INTEGER        NOT NULL
                        REFERENCES crawl_runs(id) ON DELETE CASCADE,
    url             TEXT           NOT NULL,
    page_type       VARCHAR(40)    NOT NULL,        -- homepage|product|solution|pillar|blog|about|contact|utility
    base_weight     NUMERIC(6,3)   NOT NULL DEFAULT 0,   -- from page_type (config/prioritization.yaml)
    traffic_signal  NUMERIC(10,3)  NOT NULL DEFAULT 0,   -- internal-link count now; GSC export later
    final_score     NUMERIC(12,3)  NOT NULL DEFAULT 0,   -- base_weight × traffic_signal
    final_rank      INTEGER,                             -- 1 = highest priority; NULL until ranked
    selected        BOOLEAN        NOT NULL DEFAULT FALSE, -- in the top-N cut?
    detail          JSONB          NOT NULL DEFAULT '{}',  -- ranking inputs for observability
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_page_priorities_run  ON page_priorities (run_id);
CREATE INDEX IF NOT EXISTS idx_page_priorities_rank ON page_priorities (run_id, final_rank);
CREATE INDEX IF NOT EXISTS idx_page_priorities_sel  ON page_priorities (run_id) WHERE selected;
