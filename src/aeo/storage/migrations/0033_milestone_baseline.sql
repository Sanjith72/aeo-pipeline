-- Milestone verification honesty: baseline the site before claiming credit.
--
-- The plan is generated crawl-free (intelligence.brief.plan_from_brief diffs the ideal
-- blueprint against an EMPTY site), so it recommends pages the client may already have.
-- The first verification crawl then matched those pre-existing pages and reported them as
-- "we found N changes live" — claiming credit for work nobody did, permanently.
--
-- Fix: the first check for a client is a BASELINE. Anything already live at that moment is
-- recorded with status_source='baseline' and reported as "already in place", never as newly
-- verified. Only artifacts that appear AFTER the baseline count as a real, published change.
--
-- Additive + idempotent.

-- 'baseline' joins 'manual' | 'crawl' as a way a task's status was reached.
ALTER TABLE milestone_tasks DROP CONSTRAINT IF EXISTS milestone_tasks_status_source_check;
ALTER TABLE milestone_tasks
    ADD CONSTRAINT milestone_tasks_status_source_check
    CHECK (status_source IN ('manual', 'crawl', 'baseline'));

-- When this client's milestones were baselined. NULL → the next verification run is the
-- baseline. Set once, never cleared, so credit can only ever be earned after it.
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS milestones_baselined_at TIMESTAMPTZ;

-- The owner explicitly overrode a crawl verdict (un-verified something the crawl had
-- marked done). Without this the crawl silently re-flipped it on the very next run: the
-- pending_verifiable query filters on status + verify_kind but had no way to tell "never
-- touched" from "the owner disagreed" — every fresh task also carries status_source
-- 'manual' by default, so status_source alone cannot distinguish them.
ALTER TABLE milestone_tasks
    ADD COLUMN IF NOT EXISTS owner_pinned BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN milestone_tasks.owner_pinned IS
    'Owner reversed an auto-verification; the crawl must not re-flip it. Set when a '
    'verified task is manually moved back, cleared when the owner verifies it themselves.';

COMMENT ON COLUMN clients.milestones_baselined_at IS
    'When milestone verification first snapshotted this site. Tasks already satisfied then '
    'are status_source=''baseline'' (already in place); only later detections count as '
    'newly_verified. NULL means the next verify run establishes the baseline.';
