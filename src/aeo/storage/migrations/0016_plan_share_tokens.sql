-- Developer Handoff — a secure, read-only share link for a client's implementation plan.
--
-- One stable, high-entropy token per client (the "parent plan" share record), kept in its
-- own table rather than on `clients` so the sharing concern stays isolated and revocable
-- without touching the core entity. The token is the bearer secret: anyone holding the
-- /share/<token> link gets a read-only view of the milestone dashboard with NO auth — so
-- the token must be unguessable (minted with secrets.token_urlsafe; see repos/milestones).
--
-- Additive + idempotent.

CREATE TABLE IF NOT EXISTS plan_shares (
    id           BIGSERIAL    PRIMARY KEY,
    -- One share record per client (re-requesting returns the same token, never rotates).
    client_id    INTEGER      NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
    share_token  VARCHAR(64)  NOT NULL UNIQUE,
    -- Set to revoke a leaked link without deleting the row (lookups filter on NULL).
    revoked_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- The read-only resolve path: "which client owns this (active) token?", every share view.
CREATE INDEX IF NOT EXISTS idx_plan_shares_active_token
    ON plan_shares (share_token) WHERE revoked_at IS NULL;
