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
