-- Per-page AEO/SEO report — the system's final deliverable.
-- "Human Review" is a review_status flag + a report section, NOT a UI.

CREATE TABLE IF NOT EXISTS page_reports (
    id              BIGSERIAL      PRIMARY KEY,
    page_id         BIGINT         NOT NULL
                        REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id          INTEGER        NOT NULL
                        REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    summary         TEXT,                          -- headline narrative for the page
    -- Full report body: scores, gaps, recommendations, validation outcome.
    sections        JSONB          NOT NULL DEFAULT '{}',
    review_status   VARCHAR(20)    NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending','approved','rejected','could_not_improve')),
    generated_at    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    UNIQUE (page_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_page_reports_run    ON page_reports (run_id);
CREATE INDEX IF NOT EXISTS idx_page_reports_review ON page_reports (review_status);
