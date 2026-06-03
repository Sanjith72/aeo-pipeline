-- AEO Pipeline PostgreSQL schema
-- Run once via: aeo db-init  OR  docker-compose (mounts as initdb script)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── pipeline runs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    domain        TEXT        NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        TEXT        NOT NULL DEFAULT 'running',  -- running | completed | failed
    pages_crawled INT         NOT NULL DEFAULT 0,
    pages_changed INT         NOT NULL DEFAULT 0,
    error         TEXT
);

-- ── reference blueprints ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blueprints (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    domain     TEXT        NOT NULL,
    version    INT         NOT NULL DEFAULT 1,
    data       JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, version)
);
CREATE INDEX IF NOT EXISTS blueprints_domain_idx ON blueprints (domain);

-- v4 blueprint contract migration (idempotent — safe to re-run on existing DBs).
-- NOTE: the existing `version` column already serves as the blueprint version; a
-- duplicate `blueprint_version` column is intentionally NOT added to avoid two
-- sources of truth. The columns below are the genuinely-new v4 fields.
ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS engine_target VARCHAR(32) NOT NULL DEFAULT 'generic';
ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS locked_until  TIMESTAMPTZ;
ALTER TABLE blueprints ADD COLUMN IF NOT EXISTS content_hash  CHAR(64);
CREATE INDEX IF NOT EXISTS blueprints_locked_until_idx ON blueprints (locked_until);

-- ── crawled pages ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pages (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url          TEXT        NOT NULL,
    content_hash TEXT        NOT NULL,
    title        TEXT,
    headings     JSONB       NOT NULL DEFAULT '[]',
    body_text    TEXT,
    links        JSONB       NOT NULL DEFAULT '[]',
    schema_markup JSONB      NOT NULL DEFAULT '[]',
    crawled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed      BOOLEAN     NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS pages_run_id_idx ON pages (run_id);
CREATE INDEX IF NOT EXISTS pages_url_idx    ON pages (url);

-- ── coverage reports ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coverage_reports (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id         UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    page_id        UUID        NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    blueprint_id   UUID        NOT NULL REFERENCES blueprints(id),
    data           JSONB       NOT NULL,
    coverage_score FLOAT       NOT NULL,
    generated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS coverage_run_id_idx ON coverage_reports (run_id);

-- ── recommendations ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    domain          TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    type            TEXT        NOT NULL,      -- content | schema | entity | citation
    priority        TEXT        NOT NULL,      -- high | medium | low
    title           TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    implementation  TEXT        NOT NULL,
    impact_estimate FLOAT       NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS rec_run_id_idx  ON recommendations (run_id);
CREATE INDEX IF NOT EXISTS rec_domain_idx  ON recommendations (domain);

-- ── validation results ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS validation_results (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    recommendation_id   UUID        NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    is_valid            BOOLEAN     NOT NULL,
    confidence          FLOAT       NOT NULL,
    reasoning           TEXT        NOT NULL,
    word_count_ok       BOOLEAN     NOT NULL DEFAULT FALSE,
    h1_question_ok      BOOLEAN     NOT NULL DEFAULT FALSE,
    json_ld_ok          BOOLEAN     NOT NULL DEFAULT FALSE,
    validated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS val_rec_id_idx ON validation_results (recommendation_id);

-- ── content-hash change gate ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_hashes (
    url        TEXT        PRIMARY KEY,
    hash       TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
