-- v5 P5 fix: migration 0029 added the 4th status value 'closed_pending_verify' to the
-- milestone_tasks status CHECK, but the column was still VARCHAR(20) from 0015 — and that
-- value is 21 chars, so it could never actually be stored (a latent bug that only P5's
-- ticket close→verify flow exercises). Widen the column so the value fits. Additive; the
-- 0029 CHECK is unchanged. implementation_milestones.status stays VARCHAR(20)/3-state — it
-- is never assigned the 4th value (_recompute_statuses only emits pending/in_progress/
-- verified_completed, all ≤20 chars).

ALTER TABLE milestone_tasks ALTER COLUMN status TYPE VARCHAR(30);
