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
