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
