-- Implementation Milestones — the "Final Plan" turned into persisted, trackable,
-- per-site state. The plan (report.packager.build_plan) is a list of phased tasks
-- with stable ids (page:<slug> / vis:<x>); this schema pins those to a client so the
-- owner's progress survives across sessions AND the weekly verification crawl can flip
-- a task to 'verified_completed' once it detects the recommended artifact live.
--
-- Additive + idempotent. Reuses set_updated_at() from 0001. Decoupled from
-- recommendation_outcomes (#11): that engine watches per-page hash changes; this one
-- tracks the build plan and verifies a *specific* artifact's presence (a page slug, an
-- offering name, a heading) — a narrower, non-circular signal (see milestone_verify.py).

CREATE TABLE IF NOT EXISTS implementation_milestones (
    id            BIGSERIAL    PRIMARY KEY,
    client_id     INTEGER      NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    -- Stable phase key from build_plan (week_1 | week_2_4 | later). One milestone per
    -- phase per client, so a regenerated plan re-syncs onto the same rows.
    milestone_key VARCHAR(40)  NOT NULL,
    title         VARCHAR(160) NOT NULL,
    blurb         TEXT,
    position      INTEGER      NOT NULL DEFAULT 0,
    -- Derived from its tasks (all verified → verified_completed; any started → in_progress).
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','in_progress','verified_completed')),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (client_id, milestone_key)
);

CREATE TABLE IF NOT EXISTS milestone_tasks (
    id              BIGSERIAL    PRIMARY KEY,
    milestone_id    BIGINT       NOT NULL
                        REFERENCES implementation_milestones(id) ON DELETE CASCADE,
    -- Stable plan task id (page:<slug> | vis:<x>). Unique per milestone so progress and
    -- auto-verification survive plan regeneration.
    task_key        VARCHAR(255) NOT NULL,
    label           VARCHAR(255) NOT NULL,
    action_required TEXT,
    how_to          TEXT,
    -- What the weekly crawl looks for to AUTO-verify the change is live:
    --   page    → verify_target is a URL slug; verified when that page exists on the site
    --   service → verify_target is an offering name; verified when site_facts lists it
    --   heading → verify_target is heading/nav text; verified when present live
    --   manual  → no on-site signal (e.g. Google Business Profile); owner-attested only
    verify_kind     VARCHAR(12)  NOT NULL DEFAULT 'manual'
                        CHECK (verify_kind IN ('page','service','heading','manual')),
    verify_target   VARCHAR(512),
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','in_progress','verified_completed')),
    -- How the CURRENT status was reached: 'manual' (owner clicked) or 'crawl' (the weekly
    -- verification detected the artifact live). Mirrors recommendation_outcomes' honesty —
    -- we record how we know, never silently claiming a crawl verdict for a manual one.
    status_source   VARCHAR(12)  NOT NULL DEFAULT 'manual'
                        CHECK (status_source IN ('manual','crawl')),
    detected_run_id INTEGER      REFERENCES crawl_runs(id) ON DELETE SET NULL,
    detected_at     TIMESTAMPTZ,
    position        INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (milestone_id, task_key)
);

CREATE INDEX IF NOT EXISTS idx_milestones_client     ON implementation_milestones (client_id);
CREATE INDEX IF NOT EXISTS idx_milestone_tasks_owner ON milestone_tasks (milestone_id);
-- The weekly verifier's lookup: this client's not-yet-verified, on-site-checkable tasks.
CREATE INDEX IF NOT EXISTS idx_milestone_tasks_status ON milestone_tasks (status, verify_kind);

DROP TRIGGER IF EXISTS trg_milestones_updated_at ON implementation_milestones;
CREATE TRIGGER trg_milestones_updated_at
    BEFORE UPDATE ON implementation_milestones
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_milestone_tasks_updated_at ON milestone_tasks;
CREATE TRIGGER trg_milestone_tasks_updated_at
    BEFORE UPDATE ON milestone_tasks
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
