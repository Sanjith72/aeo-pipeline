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
