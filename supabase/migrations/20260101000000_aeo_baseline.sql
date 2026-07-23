-- AEO pipeline — Supabase baseline (GENERATED — do not edit by hand).
-- Source of truth: src/aeo/storage/migrations/*.sql
-- Regenerate with: python scripts/export_supabase_baseline.py
-- Includes migrations 0001..0031.

-- schema_versions bootstrap (mirrors src/aeo/storage/migrate.py) so the app's own
-- migration runner recognises everything below as already applied.
CREATE TABLE IF NOT EXISTS schema_versions (
    version     VARCHAR(20)  PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- ═══ 0001_init ══════════════════════════════════════════════
-- Base reference tables + crawl_runs + crawled_pages.
-- Adapted from the legacy schema.sql but renamed for clarity and FK'd properly.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ─── Clients ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id           SERIAL        PRIMARY KEY,
    name         VARCHAR(255)  NOT NULL UNIQUE,
    domain       VARCHAR(255)  NOT NULL UNIQUE,
    website_url  VARCHAR(2048) NOT NULL,
    industry     VARCHAR(255)  DEFAULT 'Cybersecurity',
    notes        TEXT,
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_clients_updated_at ON clients;
CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ─── Competitors ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitors (
    id           SERIAL        PRIMARY KEY,
    name         VARCHAR(255)  NOT NULL UNIQUE,
    domain       VARCHAR(255)  NOT NULL UNIQUE,
    website_url  VARCHAR(2048) NOT NULL,
    category     VARCHAR(255),
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_competitors_updated_at ON competitors;
CREATE TRIGGER trg_competitors_updated_at
    BEFORE UPDATE ON competitors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ─── Crawl runs ───────────────────────────────────────────────────────────
-- First-class entity so pages have a real FK (legacy used a bare VARCHAR).
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            SERIAL        PRIMARY KEY,
    run_key       VARCHAR(64)   NOT NULL UNIQUE,
    label         VARCHAR(255),
    started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(20)   NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','succeeded','failed','partial')),
    notes         TEXT
);


-- ─── Crawled pages ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawled_pages (
    id                 BIGSERIAL      PRIMARY KEY,

    client_id          INTEGER        REFERENCES clients(id)     ON DELETE CASCADE,
    competitor_id      INTEGER        REFERENCES competitors(id) ON DELETE CASCADE,
    run_id             INTEGER        NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,

    url                VARCHAR(2048)  NOT NULL,
    url_normalized     VARCHAR(2048)  NOT NULL,
    content_hash       CHAR(64),

    raw_html           TEXT,
    markdown_content   TEXT,
    page_title         VARCHAR(512),
    meta_description   TEXT,

    http_status        INTEGER,
    fetch_duration_ms  INTEGER,
    crawl_status       VARCHAR(20)    NOT NULL DEFAULT 'success'
                           CHECK (crawl_status IN ('success','failed','partial','skipped')),
    error_message      TEXT,

    crawled_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_single_owner CHECK (
        (client_id IS NOT NULL AND competitor_id IS NULL) OR
        (client_id IS NULL     AND competitor_id IS NOT NULL)
    ),
    CONSTRAINT uq_url_per_run UNIQUE (url_normalized, run_id)
);

CREATE INDEX IF NOT EXISTS idx_pages_client      ON crawled_pages (client_id);
CREATE INDEX IF NOT EXISTS idx_pages_competitor  ON crawled_pages (competitor_id);
CREATE INDEX IF NOT EXISTS idx_pages_run         ON crawled_pages (run_id);
CREATE INDEX IF NOT EXISTS idx_pages_url_norm    ON crawled_pages (url_normalized);
CREATE INDEX IF NOT EXISTS idx_pages_hash        ON crawled_pages (content_hash);
CREATE INDEX IF NOT EXISTS idx_pages_status      ON crawled_pages (crawl_status);
CREATE INDEX IF NOT EXISTS idx_pages_crawled_at  ON crawled_pages (crawled_at DESC);


-- ─── Seed reference data ─────────────────────────────────────────────────
INSERT INTO clients (name, domain, website_url, industry)
VALUES ('Securin', 'securin.io', 'https://securin.io',
        'Cybersecurity / Preemptive Exposure Validation')
ON CONFLICT (name) DO NOTHING;

INSERT INTO competitors (name, domain, website_url, category) VALUES
    ('SecureLayer7',   'securelayer7.net',  'https://www.securelayer7.net',  'Penetration Testing & ASV'),
    ('XM Cyber',       'xmcyber.com',       'https://xmcyber.com',           'Exposure Management'),
    ('AttackIQ',       'attackiq.com',      'https://www.attackiq.com',      'Breach & Attack Simulation'),
    ('Pentera',        'pentera.io',        'https://pentera.io',            'Automated Security Validation'),
    ('Cymulate',       'cymulate.com',      'https://cymulate.com',          'Continuous Security Validation'),
    ('Hive Pro',       'hivepro.com',       'https://www.hivepro.com',       'Threat Exposure Management'),
    ('Picus Security', 'picussecurity.com', 'https://www.picussecurity.com', 'Breach & Attack Simulation'),
    ('Ridge Security', 'ridgesecurity.ai',  'https://ridgesecurity.ai',      'Automated Penetration Testing')
ON CONFLICT (name) DO NOTHING;
INSERT INTO schema_versions (version, name) VALUES ('0001', 'init') ON CONFLICT (version) DO NOTHING;

-- ═══ 0002_extractions_and_scores ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0002', 'extractions_and_scores') ON CONFLICT (version) DO NOTHING;

-- ═══ 0003_jobs ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0003', 'jobs') ON CONFLICT (version) DO NOTHING;

-- ═══ 0004_prioritization ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0004', 'prioritization') ON CONFLICT (version) DO NOTHING;

-- ═══ 0005_gap_and_recs ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0005', 'gap_and_recs') ON CONFLICT (version) DO NOTHING;

-- ═══ 0006_reports ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0006', 'reports') ON CONFLICT (version) DO NOTHING;

-- ═══ 0007_observability ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0007', 'observability') ON CONFLICT (version) DO NOTHING;

-- ═══ 0008_rubric_v3_10criteria ══════════════════════════════════════════════
-- v3 rubric expansion: 8 → 10 criteria (max 40 → 50).
-- Adds the two new per-criterion columns (render_accessibility, answer_readability)
-- and bumps the max_possible_score default. The new columns are NULLABLE so that
-- pre-v3 rows (rubric_version '1.0', 8 criteria) remain valid; the CHECK permits
-- NULL or a 1-5 tier. Additive + idempotent (ADD COLUMN IF NOT EXISTS).

ALTER TABLE rubric_scores_v2
    ADD COLUMN IF NOT EXISTS render_accessibility_score SMALLINT
        CHECK (render_accessibility_score IS NULL OR render_accessibility_score BETWEEN 1 AND 5);

ALTER TABLE rubric_scores_v2
    ADD COLUMN IF NOT EXISTS answer_readability_score SMALLINT
        CHECK (answer_readability_score IS NULL OR answer_readability_score BETWEEN 1 AND 5);

-- New scores carry the v3 max (50); existing rows keep their stored value.
ALTER TABLE rubric_scores_v2 ALTER COLUMN max_possible_score SET DEFAULT 50;


-- ─── Refresh the convenience view to surface the two new criteria ─────────
-- CREATE OR REPLACE cannot insert columns mid-list, so drop + recreate to keep
-- the score columns grouped together in their rubric order.
DROP VIEW IF EXISTS page_score_view;

CREATE VIEW page_score_view AS
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
    s.render_accessibility_score,
    s.answer_readability_score,
    s.scored_at
FROM crawled_pages p
LEFT JOIN clients     cl ON cl.id = p.client_id
LEFT JOIN competitors c  ON c.id  = p.competitor_id
JOIN crawl_runs       r  ON r.id  = p.run_id
LEFT JOIN rubric_scores_v2 s ON s.page_id = p.id;
INSERT INTO schema_versions (version, name) VALUES ('0008', 'rubric_v3_10criteria') ON CONFLICT (version) DO NOTHING;

-- ═══ 0009_v4_reference_architecture ══════════════════════════════════════════════
-- v4 Reference Architecture: the versioned blueprint, site-level Coverage Diff,
-- the validated-wins citation log + criteria-refinement proposals, and the
-- site-level report. Additive and idempotent — no v3 table is altered in a way
-- that changes existing semantics.

-- ─── Blueprints: the versioned, per-topic ideal site ──────────────────────────
-- One row per (topic, version). content_hash is the hash of the blueprint INPUTS
-- (topic + framework version + competitors + structure); identical inputs reuse a
-- version, any structural change bumps it. `body` holds the full Blueprint JSON
-- (reference.blueprint.Blueprint.to_jsonb()).
CREATE TABLE IF NOT EXISTS blueprints (
    id                BIGSERIAL     PRIMARY KEY,
    topic             VARCHAR(120)  NOT NULL,
    version           INTEGER       NOT NULL,
    generator         VARCHAR(80)   NOT NULL DEFAULT 'deterministic',
    framework_version VARCHAR(40)   NOT NULL DEFAULT '0',
    content_hash      CHAR(64)      NOT NULL,
    competitors       JSONB         NOT NULL DEFAULT '[]',
    body              JSONB         NOT NULL DEFAULT '{}',
    notes             TEXT,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    UNIQUE (topic, version)
);

CREATE INDEX IF NOT EXISTS idx_blueprints_topic ON blueprints (topic, version DESC);
CREATE INDEX IF NOT EXISTS idx_blueprints_hash  ON blueprints (topic, content_hash);

-- Pin every run to the blueprint version it was measured against, so a score
-- jump can be read as "new baseline" vs. "real change". Nullable: pre-v4 runs and
-- runs with the generator disabled have no pinned blueprint.
ALTER TABLE crawl_runs
    ADD COLUMN IF NOT EXISTS blueprint_id BIGINT REFERENCES blueprints(id);


-- ─── Coverage diffs: site-level gap (discovered sitemap vs ideal sitemap) ──────
-- One row per run. `detail` holds the missing/thin/matched node lists.
CREATE TABLE IF NOT EXISTS coverage_diffs (
    id              BIGSERIAL      PRIMARY KEY,
    run_id          INTEGER        NOT NULL
                        REFERENCES crawl_runs(id) ON DELETE CASCADE,
    blueprint_id    BIGINT         REFERENCES blueprints(id) ON DELETE SET NULL,
    target_id       INTEGER,                       -- the client target (FK-free: targets is seed data)
    coverage_pct    NUMERIC(5,1)   NOT NULL DEFAULT 0,
    missing_count   INTEGER        NOT NULL DEFAULT 0,
    thin_count      INTEGER        NOT NULL DEFAULT 0,
    detail          JSONB          NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- UNIQUE(run_id) already creates the btree index that run_id lookups use, so
    -- no separate single-column index is declared (it would be pure write overhead).
    UNIQUE (run_id)
);


-- ─── Citation results: the validated-wins real-world signal ───────────────────
-- Did a page (or its proposed rewrite) actually get cited for its target
-- question? Feeds the criteria-refinement proposals.
CREATE TABLE IF NOT EXISTS citation_results (
    id              BIGSERIAL      PRIMARY KEY,
    page_id         BIGINT
                        REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id          INTEGER
                        REFERENCES crawl_runs(id) ON DELETE CASCADE,
    url             TEXT           NOT NULL,
    question        TEXT           NOT NULL,
    cited           BOOLEAN        NOT NULL DEFAULT FALSE,
    engine          VARCHAR(40)    NOT NULL DEFAULT 'perplexity',
    evidence        JSONB          NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_citation_results_page ON citation_results (page_id);
CREATE INDEX IF NOT EXISTS idx_citation_results_run  ON citation_results (run_id);
CREATE INDEX IF NOT EXISTS idx_citation_results_cited ON citation_results (cited);


-- ─── Criteria refinements: controlled, human-gated learning ────────────────────
-- Pages that provably get cited propose nudges to the Reference-Layer criteria
-- *definitions*. status starts 'proposed'; a human accepts/rejects. The system
-- NEVER auto-applies — that would be circular validation one level up.
CREATE TABLE IF NOT EXISTS criteria_refinements (
    id              BIGSERIAL      PRIMARY KEY,
    criterion       VARCHAR(40)    NOT NULL,
    current_target  SMALLINT,
    proposed_target SMALLINT,
    rationale       TEXT           NOT NULL,
    evidence        JSONB          NOT NULL DEFAULT '{}',
    status          VARCHAR(20)    NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed','accepted','rejected')),
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_criteria_refinements_status ON criteria_refinements (status);


-- ─── Site reports: the site-level deliverable (coverage + per-page rollup) ─────
CREATE TABLE IF NOT EXISTS site_reports (
    id              BIGSERIAL      PRIMARY KEY,
    run_id          INTEGER        NOT NULL
                        REFERENCES crawl_runs(id) ON DELETE CASCADE,
    target_id       INTEGER,
    blueprint_id    BIGINT         REFERENCES blueprints(id) ON DELETE SET NULL,
    summary         TEXT,
    sections        JSONB          NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- UNIQUE(run_id) already provides the run_id lookup index (see coverage_diffs).
    UNIQUE (run_id)
);
INSERT INTO schema_versions (version, name) VALUES ('0009', 'v4_reference_architecture') ON CONFLICT (version) DO NOTHING;

-- ═══ 0010_recommendation_outcomes ══════════════════════════════════════════════
-- Retention Engine (#11): pin every issued recommendation to the page + content
-- hash it was measured against, so a later re-crawl can detect that the user
-- actually changed the page (the "did they do it?" signal that drives retention).
--
-- Additive and idempotent — no existing table is altered destructively.
--
-- Identity note: crawled_pages is upserted per (url_normalized, run_id), so a
-- page's row id changes EVERY run. Completion detection therefore keys on the
-- stable `url_normalized`, not `page_id`. `page_id` is retained for provenance
-- (which exact issue-run row proposed the edit) and is allowed to dangle (SET
-- NULL) if that run's pages are ever pruned.

CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id               BIGSERIAL    PRIMARY KEY,
    rec_id           BIGINT       NOT NULL
                         REFERENCES recommendations(id) ON DELETE CASCADE,
    -- Provenance: the issue-run crawled_pages row. Per-run, so NOT the detection key.
    page_id          BIGINT       REFERENCES crawled_pages(id) ON DELETE SET NULL,
    -- Stable cross-run identity — this is what mark_from_recrawl matches on.
    url_normalized   TEXT         NOT NULL,
    criterion        VARCHAR(40),                 -- rubric criterion the rec addressed (NULL = cross-cutting)

    -- Baseline pinned AT THE RUN THE REC WAS ISSUED.
    baseline_run_id  INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    baseline_hash    CHAR(64),

    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','implemented','regressed','not_detected')),
    -- How an 'implemented' verdict was reached. Today only 'content_hash_changed'.
    -- TODO(retention): a criterion-plausibility verifier (re-score the criterion on
    -- the new content) would let us distinguish "they fixed THIS" from "they changed
    -- something". Until that exists we record the method honestly and never claim the
    -- criterion itself was satisfied — that would re-introduce the self-grading the v4
    -- validator exists to avoid.
    detection_method VARCHAR(40),
    detected_run_id  INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    detected_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Playbook-specified lookup: pending outcomes for a page row, by status.
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_page_status
    ON recommendation_outcomes (page_id, status);
-- The real detection lookup: "any pending outcomes for this URL?" each re-crawl.
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_url_status
    ON recommendation_outcomes (url_normalized, status);
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_rec
    ON recommendation_outcomes (rec_id);
INSERT INTO schema_versions (version, name) VALUES ('0010', 'recommendation_outcomes') ON CONFLICT (version) DO NOTHING;

-- ═══ 0011_events ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0011', 'events') ON CONFLICT (version) DO NOTHING;

-- ═══ 0012_plan_states ══════════════════════════════════════════════
-- B1 (retention foundation): a server home for the interactive plan so progress is no
-- longer trapped in one browser's localStorage. A plan_state is the durable, resumable
-- artifact behind the /plan/<id> link — it carries the rendered plan + a profile snapshot,
-- the canonical AEO score at issue, and the set of completed task ids.
--
-- Identity is an unguessable minted token (repos/plan_state.new_id), so the link works for
-- the no-website path too (which has no run_id) and is safe to share. The optional
-- session_id powers same-browser auto-resume; run_id ties back to a deep audit when one
-- exists (and seeds the score-over-time delta in a later spec).
--
-- Additive and idempotent — no existing table is altered. set_updated_at() is from 0001.

CREATE TABLE IF NOT EXISTS plan_states (
    id             TEXT         PRIMARY KEY,                 -- minted token, in the /plan/<id> URL
    session_id     TEXT,                                     -- cookie sid that created it (auto-resume)
    run_id         INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    business_name  TEXT,
    domain         TEXT,
    plan           JSONB        NOT NULL,                    -- the StructuredPlan the view renders
    profile        JSONB,                                    -- SiteProfile snapshot (overview + score inputs)
    score_snapshot INTEGER,                                  -- canonical AEO score at issue (seeds delta later)
    done_task_ids  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Auto-resume looks up the newest plan for a returning session.
CREATE INDEX IF NOT EXISTS idx_plan_states_session ON plan_states (session_id, updated_at DESC);

DROP TRIGGER IF EXISTS trg_plan_states_updated_at ON plan_states;
CREATE TRIGGER trg_plan_states_updated_at
    BEFORE UPDATE ON plan_states
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
INSERT INTO schema_versions (version, name) VALUES ('0012', 'plan_states') ON CONFLICT (version) DO NOTHING;

-- ═══ 0013_outcome_baseline_tier ══════════════════════════════════════════════
-- Slice B (verified re-crawl moat): pin the TARGETED criterion's score tier at issue
-- time, so a re-crawl can confirm the SPECIFIC fix landed (the criterion's tier rose),
-- not merely that the page changed. The hash-only signal this replaces was the
-- self-grading the v4 validator's no-self-grading rule exists to avoid.
--
-- Additive + idempotent; nullable so pre-existing outcomes stay valid — they simply
-- can't be criterion-verified and remain pending until the recommendation is re-issued
-- (which opens a fresh outcome with a pinned baseline_tier).

ALTER TABLE recommendation_outcomes
    ADD COLUMN IF NOT EXISTS baseline_tier INTEGER;
INSERT INTO schema_versions (version, name) VALUES ('0013', 'outcome_baseline_tier') ON CONFLICT (version) DO NOTHING;

-- ═══ 0014_events_override_index ══════════════════════════════════════════════
-- Task 7 — capture user overrides of LLM/system suggestions as evaluation signals.
-- No new table: overrides ride the append-only `events` log as event_type='user_override'
-- with a standardized metadata envelope {field, suggested, chosen, source, ...}. This
-- partial index keeps the offline eval export (events.export_overrides) cheap without
-- touching the hot DAU/return-rate scans.
--
-- Additive + idempotent.

CREATE INDEX IF NOT EXISTS idx_events_override_field
    ON events ((metadata->>'field'))
    WHERE event_type = 'user_override';
INSERT INTO schema_versions (version, name) VALUES ('0014', 'events_override_index') ON CONFLICT (version) DO NOTHING;

-- ═══ 0015_implementation_milestones ══════════════════════════════════════════════
-- Implementation Milestones — the "Final Plan" turned into persisted, trackable,
-- per-site state. The plan (report.packager.build_plan) is a list of phased tasks
-- with stable ids (page:<slug> / vis:<x>); this schema pins those to a client so the
-- owner's progress survives across sessions AND the weekly verification crawl can flip
-- a task to 'verified_completed' once it detects the recommended artifact live.
--
-- Additive + idempotent. Reuses set_updated_at() from 0001. Decoupled from
-- recommendation_outcomes (#11): that engine watches per-page hash changes; this one
-- tracks the build plan and verifies a *specific* artifact's presence (a page slug, an
-- offering name, a heading) — a narrower, non-circular signal (see milestone_verify.py).

CREATE TABLE IF NOT EXISTS implementation_milestones (
    id            BIGSERIAL    PRIMARY KEY,
    client_id     INTEGER      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    -- Stable phase key from build_plan (week_1 | week_2_4 | later). One milestone per
    -- phase per client, so a regenerated plan re-syncs onto the same rows.
    milestone_key VARCHAR(40)  NOT NULL,
    title         VARCHAR(160) NOT NULL,
    blurb         TEXT,
    position      INTEGER      NOT NULL DEFAULT 0,
    -- Derived from its tasks (all verified → verified_completed; any started → in_progress).
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','in_progress','verified_completed')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, milestone_key)
);

CREATE TABLE IF NOT EXISTS milestone_tasks (
    id              BIGSERIAL    PRIMARY KEY,
    milestone_id    BIGINT       NOT NULL
                        REFERENCES implementation_milestones(id) ON DELETE CASCADE,
    -- Stable plan task id (page:<slug> | vis:<x>). Unique per milestone so progress and
    -- auto-verification survive plan regeneration.
    task_key        VARCHAR(255) NOT NULL,
    label           VARCHAR(255) NOT NULL,
    action_required TEXT,
    how_to          TEXT,
    -- What the weekly crawl looks for to AUTO-verify the change is live:
    --   page    → verify_target is a URL slug; verified when that page exists on the site
    --   service → verify_target is an offering name; verified when site_facts lists it
    --   heading → verify_target is heading/nav text; verified when present live
    --   manual  → no on-site signal (e.g. Google Business Profile); owner-attested only
    verify_kind     VARCHAR(12)  NOT NULL DEFAULT 'manual'
                        CHECK (verify_kind IN ('page','service','heading','manual')),
    verify_target   VARCHAR(512),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','in_progress','verified_completed')),
    -- How the CURRENT status was reached: 'manual' (owner clicked) or 'crawl' (the weekly
    -- verification detected the artifact live). Mirrors recommendation_outcomes' honesty —
    -- we record how we know, never silently claiming a crawl verdict for a manual one.
    status_source   VARCHAR(12)  NOT NULL DEFAULT 'manual'
                        CHECK (status_source IN ('manual','crawl')),
    detected_run_id INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    detected_at     TIMESTAMPTZ,
    position        INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (milestone_id, task_key)
);

CREATE INDEX IF NOT EXISTS idx_milestones_client     ON implementation_milestones (client_id);
CREATE INDEX IF NOT EXISTS idx_milestone_tasks_owner ON milestone_tasks (milestone_id);
-- The weekly verifier's lookup: this client's not-yet-verified, on-site-checkable tasks.
CREATE INDEX IF NOT EXISTS idx_milestone_tasks_status ON milestone_tasks (status, verify_kind);

DROP TRIGGER IF EXISTS trg_milestones_updated_at ON implementation_milestones;
CREATE TRIGGER trg_milestones_updated_at
    BEFORE UPDATE ON implementation_milestones
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_milestone_tasks_updated_at ON milestone_tasks;
CREATE TRIGGER trg_milestone_tasks_updated_at
    BEFORE UPDATE ON milestone_tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
INSERT INTO schema_versions (version, name) VALUES ('0015', 'implementation_milestones') ON CONFLICT (version) DO NOTHING;

-- ═══ 0016_plan_share_tokens ══════════════════════════════════════════════
-- Developer Handoff — a secure, read-only share link for a client's implementation plan.
--
-- One stable, high-entropy token per client (the "parent plan" share record), kept in its
-- own table rather than on `clients` so the sharing concern stays isolated and revocable
-- without touching the core entity. The token is the bearer secret: anyone holding the
-- /share/<token> link gets a read-only view of the milestone dashboard with NO auth — so
-- the token must be unguessable (minted with secrets.token_urlsafe; see repos/milestones).
--
-- Additive + idempotent.

CREATE TABLE IF NOT EXISTS plan_shares (
    id           BIGSERIAL    PRIMARY KEY,
    -- One share record per client (re-requesting returns the same token, never rotates).
    client_id    INTEGER      NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
    share_token  VARCHAR(64)  NOT NULL UNIQUE,
    -- Set to revoke a leaked link without deleting the row (lookups filter on NULL).
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- The read-only resolve path: "which client owns this (active) token?", every share view.
CREATE INDEX IF NOT EXISTS idx_plan_shares_active_token
    ON plan_shares (share_token) WHERE revoked_at IS NULL;
INSERT INTO schema_versions (version, name) VALUES ('0016', 'plan_share_tokens') ON CONFLICT (version) DO NOTHING;

-- ═══ 0017_plan_shares_rotation ══════════════════════════════════════════════
-- Revoke & Regenerate — let a client accumulate REVOKED share rows (an audit trail of
-- killed links) while still guaranteeing exactly ONE active token at a time.
--
-- 0013 declared `client_id` as a column-level UNIQUE, which allowed only a single row per
-- client — fine for mint-once, but it blocks rotation: revoke-and-regenerate must keep the
-- old row (with revoked_at set) alongside the new active one. Replace the total uniqueness
-- with a PARTIAL unique index scoped to active (non-revoked) rows.
--
-- Additive + idempotent.

ALTER TABLE plan_shares DROP CONSTRAINT IF EXISTS plan_shares_client_id_key;

-- At most one ACTIVE (revoked_at IS NULL) token per client; revoked rows are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_plan_shares_active_client
    ON plan_shares (client_id) WHERE revoked_at IS NULL;
INSERT INTO schema_versions (version, name) VALUES ('0017', 'plan_shares_rotation') ON CONFLICT (version) DO NOTHING;

-- ═══ 0018_clients_cms_type ══════════════════════════════════════════════
-- Detected publishing platform for a client's site ('wordpress' | 'shopify' | 'unknown').
--
-- Drives the "I'll do it myself" instructions in the implementation dashboard: the same
-- task gets WordPress- vs Shopify- vs CMS-agnostic copy-paste steps. Stored on `clients`
-- (a per-site fact, like `industry`) so both the owner dashboard and the read-only share
-- view render the right steps without re-crawling.
--
-- Additive + idempotent. NULL means "not detected yet" — the brief generator treats that
-- the same as 'unknown'.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS cms_type VARCHAR(32);
INSERT INTO schema_versions (version, name) VALUES ('0018', 'clients_cms_type') ON CONFLICT (version) DO NOTHING;

-- ═══ 0019_agent_runs ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0019', 'agent_runs') ON CONFLICT (version) DO NOTHING;

-- ═══ 0020_gamification_state ══════════════════════════════════════════════
-- Phase 3 gamification: derived per-session companion state. Reads verdicts from existing
-- tables (recommendation_outcomes, etc.); persists only derived state. Keyed on session_id
-- (the auth-free DAU identity, like events/plan_states). Additive + idempotent.

CREATE TABLE IF NOT EXISTS gamification_state (
    session_id        TEXT        PRIMARY KEY,
    client_id         INTEGER     REFERENCES clients(id) ON DELETE SET NULL,
    domain            TEXT,
    maturity_stage    VARCHAR(20) NOT NULL DEFAULT 'foundations'
        CHECK (maturity_stage IN ('foundations','on_radar','recommended','authority','cited_leader')),
    aeo_score         INTEGER,
    aeo_band          VARCHAR(20),
    momentum          INTEGER     NOT NULL DEFAULT 0,
    last_verified_at  TIMESTAMPTZ,
    verified_wins     INTEGER     NOT NULL DEFAULT 0,
    citations_earned  INTEGER     NOT NULL DEFAULT 0,
    track_progress    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gam_state_domain ON gamification_state (domain);

DROP TRIGGER IF EXISTS trg_gam_state_updated_at ON gamification_state;
CREATE TRIGGER trg_gam_state_updated_at
    BEFORE UPDATE ON gamification_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
INSERT INTO schema_versions (version, name) VALUES ('0020', 'gamification_state') ON CONFLICT (version) DO NOTHING;

-- ═══ 0021_gamification_awards ══════════════════════════════════════════════
-- Append-only award ledger. UNIQUE(award_type, source_table, source_id) is the anti-inflation
-- guard: reconcile re-runs never double-grant an award sourced from the same verdict row. Every
-- award comes from a real verdict (source_id NOT NULL), so the count can never drift from reality.

CREATE TABLE IF NOT EXISTS gamification_awards (
    id            BIGSERIAL    PRIMARY KEY,
    session_id    TEXT         NOT NULL,
    client_id     INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    award_type    VARCHAR(30)  NOT NULL
        CHECK (award_type IN ('verified_win','citation','status_tier','maturity_up')),
    source_table  VARCHAR(40)  NOT NULL,
    source_id     BIGINT       NOT NULL,
    criterion     VARCHAR(40),
    tier_before   SMALLINT,
    tier_after    SMALLINT,
    score_delta   INTEGER,
    detail        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (award_type, source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_gam_awards_session ON gamification_awards (session_id, created_at DESC);
INSERT INTO schema_versions (version, name) VALUES ('0021', 'gamification_awards') ON CONFLICT (version) DO NOTHING;

-- ═══ 0022_achievements ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0022', 'achievements') ON CONFLICT (version) DO NOTHING;

-- ═══ 0023_recommendation_predicted_lift ══════════════════════════════════════════════
-- Feature #2 (predicted score lift): persist the per-recommendation PREDICTED
-- rubric-point lift AT ISSUE TIME, so the UI can show "+X pts" BEFORE the user
-- acts, and so a later re-crawl can hold the prediction accountable (predicted
-- vs actual) for calibration.
--
-- The lift is derived deterministically by the Validation loop's own simulate ->
-- re-score machinery (one rec applied at a time, cumulative-marginal in gap
-- priority order), so per-rec deltas never double-count a criterion's headroom.
-- Unit is rubric points (the page total's native 0-50 scale; all criterion
-- weights are 1.0, so a tier gain a->b is exactly b-a points).
--
--   predicted_delta  point estimate (NULL = the simulator could not estimate;
--                    render "—", never a fake 0)
--   predicted_low    tier-short conservative bound  = max(0, point - weight)
--   predicted_high   = point (the bounded-to-target, optimistic case)
--   predicted_basis  provenance:
--                      'simulated'             — a real positive estimate
--                      'no_deterministic_lift' — applied but the total did not move
--                                                (already at/above target / competitor pressure)
--                      'unknown'               — advisory-only rec, nothing to simulate
--
-- The matching ACTUAL side lives on recommendation_outcomes: detected_tier pins
-- the criterion's freshly re-scored tier when a re-crawl confirms the fix, so
-- actual_delta = (detected_tier - baseline_tier) * weight joins to the prediction
-- by rec_id.
--
-- Additive + idempotent; every column is nullable so pre-existing rows stay valid
-- (they simply carry no prediction until the recommendation is re-issued).

ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS predicted_delta NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_low   NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_high  NUMERIC(6,3),
    ADD COLUMN IF NOT EXISTS predicted_basis VARCHAR(30);

ALTER TABLE recommendation_outcomes
    ADD COLUMN IF NOT EXISTS detected_tier SMALLINT;
INSERT INTO schema_versions (version, name) VALUES ('0023', 'recommendation_predicted_lift') ON CONFLICT (version) DO NOTHING;

-- ═══ 0024_milestone_task_context ══════════════════════════════════════════════
-- Carry the plan task's "Where you are now" (current_state) and the AI/human
-- implementation prompts onto persisted milestone tasks.
--
-- build_plan (report.packager) sets both on every page task, but plan_to_milestones
-- (report.milestones) dropped them when turning the plan into milestone specs — so the
-- implementation dashboard's "Show me how" expander couldn't show the "Where you are now"
-- line or the "Doing it with AI" prompt that the no-domain plan view already has. Persist
-- them so the unified TaskHowTo expander renders the same superset on both paths.
--
-- Additive + idempotent. Both NULL-able: page tasks carry prompts, off-site visibility
-- tasks (vis:*) don't, and current_state may be absent on an older re-synced plan. JSONB
-- to match the plan_states.plan / .profile round-trip (json.dumps on write, psycopg2's
-- default jsonb typecaster hands it back as a dict on read).

ALTER TABLE milestone_tasks ADD COLUMN IF NOT EXISTS current_state TEXT;
ALTER TABLE milestone_tasks ADD COLUMN IF NOT EXISTS prompts       JSONB;
INSERT INTO schema_versions (version, name) VALUES ('0024', 'milestone_task_context') ON CONFLICT (version) DO NOTHING;

-- ═══ 0025_pgvector_embeddings ══════════════════════════════════════════════
-- src/aeo/storage/migrations/0025_pgvector_embeddings.sql
-- Semantic-search substrate: pgvector + content_embeddings.
--
-- Guarded: on servers where the pgvector extension is not installed (e.g. a plain local
-- Postgres), this migration logs a NOTICE and creates nothing — the embeddings repo and
-- the agent's semantic_search tool detect the missing table and degrade to keyword search.
-- On Supabase (pgvector preinstalled) the table + HNSW index are created.
--
-- Dimension 768 = Gemini gemini-embedding-001 with output_dimensionality=768 (also the
-- native size of text-embedding-004 and of sentence-transformers all-mpnet-base-v2).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
        EXECUTE 'CREATE EXTENSION IF NOT EXISTS vector';
    ELSE
        RAISE NOTICE 'pgvector not available on this server — content_embeddings skipped (semantic search disabled)';
        RETURN;
    END IF;

    EXECUTE $ddl$
        CREATE TABLE IF NOT EXISTS content_embeddings (
            id              BIGSERIAL     PRIMARY KEY,
            page_id         BIGINT        REFERENCES crawled_pages(id) ON DELETE CASCADE,
            url             VARCHAR(2048) NOT NULL,
            kind            VARCHAR(32)   NOT NULL DEFAULT 'page'
                                CHECK (kind IN ('page','chunk','blueprint_page','query')),
            chunk_ix        INTEGER       NOT NULL DEFAULT 0,
            content_sha256  CHAR(64)      NOT NULL,
            model           VARCHAR(120)  NOT NULL,
            embedding       vector(768)   NOT NULL,
            created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_embedding_slot UNIQUE (url, kind, chunk_ix)
        )
    $ddl$;

    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_embeddings_page ON content_embeddings (page_id)';

    -- HNSW needs pgvector >= 0.5. If this server ships an older build, skip the index —
    -- at this schema's scale (hundreds of pages) a sequential scan is fast enough.
    BEGIN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON content_embeddings USING hnsw (embedding vector_cosine_ops)';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'hnsw index unavailable (pgvector < 0.5?) — skipped';
    END;
END $$;
INSERT INTO schema_versions (version, name) VALUES ('0025', 'pgvector_embeddings') ON CONFLICT (version) DO NOTHING;

-- ═══ 0026_rls_hardening ══════════════════════════════════════════════
-- src/aeo/storage/migrations/0026_rls_hardening.sql
-- Row Level Security hardening for hosted Postgres (Supabase).
--
-- The app connects as the table OWNER (postgres on Supabase, aeo locally), and owners
-- bypass non-FORCE RLS — so enabling RLS changes nothing for the app itself. What it
-- does close is Supabase's auto-generated Data API (PostgREST): the anon/authenticated
-- roles get RLS with NO policies (deny-all) and their blanket grants revoked, so the
-- database is reachable only through this backend. On a plain local Postgres the role
-- branches are skipped and enabling RLS is a harmless no-op for the owner.
--
-- NOTE for future migrations: new tables must ALTER TABLE ... ENABLE ROW LEVEL SECURITY
-- themselves (this migration only covers tables that exist when it runs).
--
-- DEPLOY CONSTRAINT: DATABASE_URL must connect as the table owner (or a BYPASSRLS
-- role). A least-privilege non-owner app role would be denied by the enable-all +
-- no-policies combination below.

DO $$
DECLARE
    t RECORD;
BEGIN
    -- Ownership-filtered: only ALTER tables this role can actually alter, so a stray
    -- extension-owned table in public (e.g. postgis) can't abort the migration.
    FOR t IN
        SELECT c.relname AS tablename
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND pg_has_role(c.relowner, 'USAGE')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM authenticated';
    END IF;
END $$;
INSERT INTO schema_versions (version, name) VALUES ('0026', 'rls_hardening') ON CONFLICT (version) DO NOTHING;

-- ═══ 0027_skill_scores ══════════════════════════════════════════════
-- v5 CH-04/CH-13 — the 5-skill derived scoring layer (contract: docs/V5_CONTRACTS.md §a).
-- Strictly additive: rubric_scores_v2, its 10 tier columns, and RUBRIC_VERSION are
-- untouched. Skill rows are a derived, separately-versioned view over criterion tiers +
-- evidence (recomputable), keyed to the same page/run pair. The six score columns are the
-- queryable summary; `detail` holds the full per-skill payload (suggestions + evidence).

CREATE TABLE IF NOT EXISTS skill_scores (
    id             BIGSERIAL   PRIMARY KEY,
    page_id        BIGINT      NOT NULL REFERENCES crawled_pages(id) ON DELETE CASCADE,
    run_id         INTEGER     NOT NULL REFERENCES crawl_runs(id)    ON DELETE CASCADE,
    skills_version VARCHAR(10) NOT NULL DEFAULT '1.0',

    messaging_score            SMALLINT NOT NULL CHECK (messaging_score            BETWEEN 0 AND 100),
    conversion_score           SMALLINT NOT NULL CHECK (conversion_score           BETWEEN 0 AND 100),
    discovery_visibility_score SMALLINT NOT NULL CHECK (discovery_visibility_score BETWEEN 0 AND 100),
    proof_trust_score          SMALLINT NOT NULL CHECK (proof_trust_score          BETWEEN 0 AND 100),
    structure_ux_score         SMALLINT NOT NULL CHECK (structure_ux_score         BETWEEN 0 AND 100),
    overall_score              SMALLINT NOT NULL CHECK (overall_score              BETWEEN 0 AND 100),

    detail         JSONB       NOT NULL DEFAULT '{}',
    scored_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (page_id, run_id, skills_version)
);

CREATE INDEX IF NOT EXISTS idx_skill_scores_run ON skill_scores (run_id);

-- 0026 convention: new tables self-enable RLS (owner bypasses; closes the Data API).
ALTER TABLE skill_scores ENABLE ROW LEVEL SECURITY;
INSERT INTO schema_versions (version, name) VALUES ('0027', 'skill_scores') ON CONFLICT (version) DO NOTHING;

-- ═══ 0028_packs ══════════════════════════════════════════════
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
INSERT INTO schema_versions (version, name) VALUES ('0028', 'packs') ON CONFLICT (version) DO NOTHING;

-- ═══ 0029_ticket_fields ══════════════════════════════════════════════
-- v5 CH-08/CH-13 — turn milestone_tasks into full tickets (contract:
-- docs/V5_CONTRACTS.md §c): assignee + target date + the page/skill the ticket targets +
-- the before/after skill scores (0-100, from skill_scores) the close-triggered re-crawl
-- proves. Additive, 0024-pattern. The status vocabulary gains 'closed_pending_verify'
-- (owner says done → verification re-crawl pending); the DROP+ADD pair below is the
-- idempotent way to widen an inline CHECK.

ALTER TABLE milestone_tasks
    ADD COLUMN IF NOT EXISTS assignee       VARCHAR(120),
    ADD COLUMN IF NOT EXISTS target_date    DATE,
    ADD COLUMN IF NOT EXISTS page_url       TEXT,
    ADD COLUMN IF NOT EXISTS skill          VARCHAR(24)
        CHECK (skill IS NULL OR skill IN
               ('messaging','conversion','discovery_visibility','proof_trust','structure_ux')),
    ADD COLUMN IF NOT EXISTS baseline_score SMALLINT
        CHECK (baseline_score IS NULL OR baseline_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS current_score  SMALLINT
        CHECK (current_score IS NULL OR current_score BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS closed_at      TIMESTAMPTZ;

ALTER TABLE milestone_tasks DROP CONSTRAINT IF EXISTS milestone_tasks_status_check;
ALTER TABLE milestone_tasks ADD CONSTRAINT milestone_tasks_status_check
    CHECK (status IN ('pending','in_progress','closed_pending_verify','verified_completed'));

-- The async board's "my open tickets" lookup.
CREATE INDEX IF NOT EXISTS idx_milestone_tasks_assignee
    ON milestone_tasks (assignee) WHERE assignee IS NOT NULL;
INSERT INTO schema_versions (version, name) VALUES ('0029', 'ticket_fields') ON CONFLICT (version) DO NOTHING;

-- ═══ 0030_users_entitlements ══════════════════════════════════════════════
-- v5 CH-02b/CH-07/CH-13 — the identity spine + entitlements (contract:
-- docs/V5_CONTRACTS.md §d and "Identity spine"). app_users.id is the Supabase JWT `sub`
-- claim (UUID); session_id bridges the pre-auth aeo_sid cookie so anonymous work
-- (plan_states, events, gamification) can be claimed at signup. Entitlement enforcement
-- is application-level SQL in authenticated routes — the backend connects as table owner
-- and owners bypass non-FORCE RLS (0026); RLS here only closes the Supabase Data API.
-- Payments are stubbed by decision (§9.2): grants arrive via source='manual'/'promo'.

CREATE TABLE IF NOT EXISTS app_users (
    id         UUID        PRIMARY KEY,
    email      TEXT,
    session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_users_session ON app_users (session_id)
    WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS entitlements (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    domain     TEXT        NOT NULL,
    scope      VARCHAR(24) NOT NULL
                   CHECK (scope IN ('free_overview','pack','all_packs','tickets')),
    pack_index INTEGER,    -- NULL unless scope='pack'
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    source     VARCHAR(24) NOT NULL DEFAULT 'manual',

    -- Idempotent grants: re-granting the same unlock is a no-op upsert target.
    -- NULLS NOT DISTINCT so two scope='all_packs' rows (pack_index NULL) collide.
    UNIQUE NULLS NOT DISTINCT (user_id, domain, scope, pack_index)
);

CREATE INDEX IF NOT EXISTS idx_entitlements_user_domain ON entitlements (user_id, domain);

ALTER TABLE app_users    ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
INSERT INTO schema_versions (version, name) VALUES ('0030', 'users_entitlements') ON CONFLICT (version) DO NOTHING;

-- ═══ 0031_milestones_owner ══════════════════════════════════════════════
-- v5 P4 identity bridge (CH-07): stamp milestone ownership so P5 can flip per-user
-- enforcement on without another migration + backfill. Additive ONLY — GET /api/milestones
-- stays domain-keyed/anonymous in P4 (gating it now would break the shipped dashboard).
-- implementation_milestones already self-enabled RLS at 0026, so no new ENABLE line here;
-- the backend connects as table owner and owners bypass non-FORCE RLS (app-level checks).

ALTER TABLE implementation_milestones
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES app_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_milestones_owner_user
    ON implementation_milestones (owner_user_id) WHERE owner_user_id IS NOT NULL;
INSERT INTO schema_versions (version, name) VALUES ('0031', 'milestones_owner') ON CONFLICT (version) DO NOTHING;
