-- Status-tier credentials (GitHub/Linear flavored), declaratively bound to real metrics and
-- earned once per session. Seeded with the two AEO-score milestones; more land with Plan 3B.

CREATE TABLE IF NOT EXISTS achievement_definitions (
    code             VARCHAR(40)  PRIMARY KEY,
    title            VARCHAR(120) NOT NULL,
    description      TEXT,
    unlock_metric    VARCHAR(40)  NOT NULL,   -- 'aeo_score' | 'coverage_pct' | 'citation_count'
    unlock_threshold NUMERIC      NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS achievement_unlocks (
    id            BIGSERIAL   PRIMARY KEY,
    session_id    TEXT        NOT NULL,
    code          VARCHAR(40) NOT NULL REFERENCES achievement_definitions(code),
    unlocked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_run_id INTEGER     REFERENCES crawl_runs(id) ON DELETE SET NULL,
    UNIQUE (session_id, code)
);

INSERT INTO achievement_definitions (code, title, description, unlock_metric, unlock_threshold) VALUES
    ('recommended', 'Recommended', 'Your AEO Score crossed into Recommended.', 'aeo_score', 60),
    ('top_answer',  'Top Answer',  'Your AEO Score reached Top Answer.',       'aeo_score', 80)
ON CONFLICT (code) DO NOTHING;
