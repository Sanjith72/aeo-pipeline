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
