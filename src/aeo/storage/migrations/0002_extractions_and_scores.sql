-- Extraction bundles (raw extractor output) + rubric scores aligned to the
-- 8-criterion / 1-5 scale rubric.

-- ─── Extractions: one row per page; JSONB blob keyed by extractor name ────
CREATE TABLE IF NOT EXISTS page_extractions (
    id                 BIGSERIAL      PRIMARY KEY,
    page_id            BIGINT         NOT NULL UNIQUE
                           REFERENCES crawled_pages(id) ON DELETE CASCADE,
    -- Each top-level key is an extractor name (headings, schema_jsonld, qa_blocks…)
    extracted          JSONB          NOT NULL DEFAULT '{}',
    extractor_version  VARCHAR(20)    NOT NULL DEFAULT '1',
    extracted_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extractions_page  ON page_extractions (page_id);
-- Useful for "find pages missing FAQ schema": extracted->'schema_jsonld'->'types'
CREATE INDEX IF NOT EXISTS idx_extractions_gin   ON page_extractions USING GIN (extracted);


-- ─── Rubric scores: 8 criteria × 1-5 scale, max 40 ───────────────────────
CREATE TABLE IF NOT EXISTS rubric_scores_v2 (
    id                          BIGSERIAL      PRIMARY KEY,
    page_id                     BIGINT         NOT NULL
                                    REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id                      INTEGER        NOT NULL
                                    REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    rubric_version              VARCHAR(20)    NOT NULL DEFAULT '1.0',

    schema_markup_score         SMALLINT       NOT NULL CHECK (schema_markup_score      BETWEEN 1 AND 5),
    qa_blocks_score             SMALLINT       NOT NULL CHECK (qa_blocks_score          BETWEEN 1 AND 5),
    stats_in_html_score         SMALLINT       NOT NULL CHECK (stats_in_html_score      BETWEEN 1 AND 5),
    entity_consistency_score    SMALLINT       NOT NULL CHECK (entity_consistency_score BETWEEN 1 AND 5),
    heading_structure_score     SMALLINT       NOT NULL CHECK (heading_structure_score  BETWEEN 1 AND 5),
    content_depth_score         SMALLINT       NOT NULL CHECK (content_depth_score      BETWEEN 1 AND 5),
    citation_signals_score      SMALLINT       NOT NULL CHECK (citation_signals_score   BETWEEN 1 AND 5),
    load_speed_score            SMALLINT       NOT NULL CHECK (load_speed_score         BETWEEN 1 AND 5),

    total_score                 SMALLINT       NOT NULL,
    max_possible_score          SMALLINT       NOT NULL DEFAULT 40,
    score_percentage            NUMERIC(5,2)   GENERATED ALWAYS AS (
        ROUND((total_score::numeric / NULLIF(max_possible_score, 0)) * 100, 2)
    ) STORED,

    priority_tier               VARCHAR(40),    -- 'Critical Rework', 'High Priority', …
    -- Per-criterion evidence: { schema_markup: { value, evidence, ... }, ... }
    evidence                    JSONB          NOT NULL DEFAULT '{}',
    scored_by                   VARCHAR(100),   -- 'deterministic' | model name | 'hybrid'
    scored_at                   TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    UNIQUE (page_id, run_id, rubric_version)
);

CREATE INDEX IF NOT EXISTS idx_scores_v2_page    ON rubric_scores_v2 (page_id);
CREATE INDEX IF NOT EXISTS idx_scores_v2_run     ON rubric_scores_v2 (run_id);
CREATE INDEX IF NOT EXISTS idx_scores_v2_total   ON rubric_scores_v2 (total_score DESC);


-- ─── Convenience view ────────────────────────────────────────────────────
CREATE OR REPLACE VIEW page_score_view AS
SELECT
    p.id              AS page_id,
    p.url,
    p.url_normalized,
    p.client_id,
    p.competitor_id,
    COALESCE(c.name, cl.name) AS owner_name,
    CASE WHEN p.client_id IS NOT NULL THEN 'client' ELSE 'competitor' END AS owner_type,
    r.run_key,
    s.total_score,
    s.score_percentage,
    s.priority_tier,
    s.schema_markup_score,
    s.qa_blocks_score,
    s.stats_in_html_score,
    s.entity_consistency_score,
    s.heading_structure_score,
    s.content_depth_score,
    s.citation_signals_score,
    s.load_speed_score,
    s.scored_at
FROM crawled_pages p
LEFT JOIN clients     cl ON cl.id = p.client_id
LEFT JOIN competitors c  ON c.id  = p.competitor_id
JOIN crawl_runs       r  ON r.id  = p.run_id
LEFT JOIN rubric_scores_v2 s ON s.page_id = p.id;
