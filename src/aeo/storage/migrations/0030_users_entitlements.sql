-- v5 CH-02b/CH-07/CH-13 — the identity spine + entitlements (contract:
-- docs/V5_CONTRACTS.md §d and "Identity spine"). app_users.id is the Supabase JWT `sub`
-- claim (UUID); session_id bridges the pre-auth aeo_sid cookie so anonymous work
-- (plan_states, events, gamification) can be claimed at signup. Entitlement enforcement
-- is application-level SQL in authenticated routes — the backend connects as table owner
-- and owners bypass non-FORCE RLS (0026); RLS here only closes the Supabase Data API.
-- Payments are stubbed by decision (§9.2): grants arrive via source='manual'/'promo'.

CREATE TABLE IF NOT EXISTS app_users (
    id         UUID        PRIMARY KEY,
    email      TEXT,
    session_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_users_session ON app_users (session_id)
    WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS entitlements (
    id         BIGSERIAL   PRIMARY KEY,
    user_id    UUID        NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    domain     TEXT        NOT NULL,
    scope      VARCHAR(24) NOT NULL
                   CHECK (scope IN ('free_overview','pack','all_packs','tickets')),
    pack_index INTEGER,    -- NULL unless scope='pack'
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    source     VARCHAR(24) NOT NULL DEFAULT 'manual',

    -- Idempotent grants: re-granting the same unlock is a no-op upsert target.
    -- NULLS NOT DISTINCT so two scope='all_packs' rows (pack_index NULL) collide.
    UNIQUE NULLS NOT DISTINCT (user_id, domain, scope, pack_index)
);

CREATE INDEX IF NOT EXISTS idx_entitlements_user_domain ON entitlements (user_id, domain);

ALTER TABLE app_users    ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
