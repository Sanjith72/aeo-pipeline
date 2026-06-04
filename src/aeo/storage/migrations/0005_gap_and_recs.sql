-- Dual-Layer Gap Analysis (Processor) + Recommender output.

-- ─── Gap analyses: one row per page/run ──────────────────────────────────
-- bestpractice_gap = 60% layer (target − actual vs Reference Layer targets),
-- competitor_gap   = 40% layer (vs best competitor page for the query intent).
CREATE TABLE IF NOT EXISTS gap_analyses (
    id                BIGSERIAL     PRIMARY KEY,
    page_id           BIGINT        NOT NULL
                          REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id            INTEGER       NOT NULL
                          REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    bestpractice_gap  NUMERIC(6,3)  NOT NULL DEFAULT 0,
    competitor_gap    NUMERIC(6,3)  NOT NULL DEFAULT 0,
    overall_gap       NUMERIC(6,3)  NOT NULL DEFAULT 0,
    -- Ordered criterion_gaps[] (prioritized deficiency list) + optional narrative.
    detail            JSONB         NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    UNIQUE (page_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_gap_analyses_page ON gap_analyses (page_id);
CREATE INDEX IF NOT EXISTS idx_gap_analyses_run  ON gap_analyses (run_id);


-- ─── Recommendations: append-style log (multiple per page, per attempt) ───
-- A page accumulates rows across criteria and Validation retry attempts, so
-- there is no natural unique key — the Validation loop inserts a fresh row per
-- attempt and updates status/scores by id.
CREATE TABLE IF NOT EXISTS recommendations (
    id              BIGSERIAL      PRIMARY KEY,
    page_id         BIGINT         NOT NULL
                        REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id          INTEGER        NOT NULL
                        REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    rec_type        VARCHAR(40)    NOT NULL,        -- 'schema' | 'content' | 'entity'  (type is reserved)
    criterion       VARCHAR(40),                    -- rubric criterion addressed; NULL = cross-cutting
    payload         JSONB          NOT NULL DEFAULT '{}',  -- the concrete proposed edit
    status          VARCHAR(20)    NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','validated','rejected','failed','needs_review')),
    attempt         SMALLINT       NOT NULL DEFAULT 1,     -- Validation retry counter (≤3)
    validated       BOOLEAN        NOT NULL DEFAULT FALSE,
    score_before    NUMERIC(6,3),                  -- rubric total before applying the edit
    score_after     NUMERIC(6,3),                  -- re-scored total on the synthetic page
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_page   ON recommendations (page_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_run    ON recommendations (run_id);
CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations (status);
