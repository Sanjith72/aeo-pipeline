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
