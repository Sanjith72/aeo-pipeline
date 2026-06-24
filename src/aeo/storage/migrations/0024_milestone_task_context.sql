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
