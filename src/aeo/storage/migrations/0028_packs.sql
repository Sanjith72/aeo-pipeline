-- v5 CH-03/CH-13 — packs: bounded, impact-ordered page groups (contract:
-- docs/V5_CONTRACTS.md §b). Pack 1 always contains the homepage (rule in
-- pipeline/packs.py, not emergent from weights); no pack exceeds MAX_PACK_PAGES (=5,
-- resolved §9.1). Membership rides the existing per-run ranking table
-- (page_priorities, keyed run_id+url) via pack_index — `selected` semantics unchanged.

CREATE TABLE IF NOT EXISTS packs (
    id           BIGSERIAL        PRIMARY KEY,
    run_id       INTEGER          NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    pack_index   INTEGER          NOT NULL,   -- 1 = homepage pack; then descending impact
    title        VARCHAR(120)     NOT NULL,
    impact_score DOUBLE PRECISION,
    page_count   INTEGER          NOT NULL DEFAULT 0,
    status       VARCHAR(20)      NOT NULL DEFAULT 'preview'
                     CHECK (status IN ('preview','unlocked','crawled','scored')),
    created_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),

    UNIQUE (run_id, pack_index)
);

CREATE INDEX IF NOT EXISTS idx_packs_run ON packs (run_id);

ALTER TABLE page_priorities ADD COLUMN IF NOT EXISTS pack_index INTEGER;

ALTER TABLE packs ENABLE ROW LEVEL SECURITY;
