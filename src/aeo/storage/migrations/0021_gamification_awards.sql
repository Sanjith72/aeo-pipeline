-- Append-only award ledger. UNIQUE(award_type, source_table, source_id) is the anti-inflation
-- guard: reconcile re-runs never double-grant an award sourced from the same verdict row. Every
-- award comes from a real verdict (source_id NOT NULL), so the count can never drift from reality.

CREATE TABLE IF NOT EXISTS gamification_awards (
    id            BIGSERIAL    PRIMARY KEY,
    session_id    TEXT         NOT NULL,
    client_id     INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    award_type    VARCHAR(30)  NOT NULL
        CHECK (award_type IN ('verified_win','citation','status_tier','maturity_up')),
    source_table  VARCHAR(40)  NOT NULL,
    source_id     BIGINT       NOT NULL,
    criterion     VARCHAR(40),
    tier_before   SMALLINT,
    tier_after    SMALLINT,
    score_delta   INTEGER,
    detail        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (award_type, source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_gam_awards_session ON gamification_awards (session_id, created_at DESC);
