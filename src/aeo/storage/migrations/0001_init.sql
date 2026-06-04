-- Base reference tables + crawl_runs + crawled_pages.
-- Adapted from the legacy schema.sql but renamed for clarity and FK'd properly.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ─── Clients ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id           SERIAL        PRIMARY KEY,
    name         VARCHAR(255)  NOT NULL UNIQUE,
    domain       VARCHAR(255)  NOT NULL UNIQUE,
    website_url  VARCHAR(2048) NOT NULL,
    industry     VARCHAR(255)  DEFAULT 'Cybersecurity',
    notes        TEXT,
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_clients_updated_at ON clients;
CREATE TRIGGER trg_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ─── Competitors ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitors (
    id           SERIAL        PRIMARY KEY,
    name         VARCHAR(255)  NOT NULL UNIQUE,
    domain       VARCHAR(255)  NOT NULL UNIQUE,
    website_url  VARCHAR(2048) NOT NULL,
    category     VARCHAR(255),
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_competitors_updated_at ON competitors;
CREATE TRIGGER trg_competitors_updated_at
    BEFORE UPDATE ON competitors
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ─── Crawl runs ───────────────────────────────────────────────────────────
-- First-class entity so pages have a real FK (legacy used a bare VARCHAR).
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            SERIAL        PRIMARY KEY,
    run_key       VARCHAR(64)   NOT NULL UNIQUE,
    label         VARCHAR(255),
    started_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(20)   NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running','succeeded','failed','partial')),
    notes         TEXT
);


-- ─── Crawled pages ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS crawled_pages (
    id                 BIGSERIAL      PRIMARY KEY,

    client_id          INTEGER        REFERENCES clients(id)     ON DELETE CASCADE,
    competitor_id      INTEGER        REFERENCES competitors(id) ON DELETE CASCADE,
    run_id             INTEGER        NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,

    url                VARCHAR(2048)  NOT NULL,
    url_normalized     VARCHAR(2048)  NOT NULL,
    content_hash       CHAR(64),

    raw_html           TEXT,
    markdown_content   TEXT,
    page_title         VARCHAR(512),
    meta_description   TEXT,

    http_status        INTEGER,
    fetch_duration_ms  INTEGER,
    crawl_status       VARCHAR(20)    NOT NULL DEFAULT 'success'
                           CHECK (crawl_status IN ('success','failed','partial','skipped')),
    error_message      TEXT,

    crawled_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_single_owner CHECK (
        (client_id IS NOT NULL AND competitor_id IS NULL) OR
        (client_id IS NULL     AND competitor_id IS NOT NULL)
    ),
    CONSTRAINT uq_url_per_run UNIQUE (url_normalized, run_id)
);

CREATE INDEX IF NOT EXISTS idx_pages_client      ON crawled_pages (client_id);
CREATE INDEX IF NOT EXISTS idx_pages_competitor  ON crawled_pages (competitor_id);
CREATE INDEX IF NOT EXISTS idx_pages_run         ON crawled_pages (run_id);
CREATE INDEX IF NOT EXISTS idx_pages_url_norm    ON crawled_pages (url_normalized);
CREATE INDEX IF NOT EXISTS idx_pages_hash        ON crawled_pages (content_hash);
CREATE INDEX IF NOT EXISTS idx_pages_status      ON crawled_pages (crawl_status);
CREATE INDEX IF NOT EXISTS idx_pages_crawled_at  ON crawled_pages (crawled_at DESC);


-- ─── Seed reference data ─────────────────────────────────────────────────
INSERT INTO clients (name, domain, website_url, industry)
VALUES ('Securin', 'securin.io', 'https://securin.io',
        'Cybersecurity / Preemptive Exposure Validation')
ON CONFLICT (name) DO NOTHING;

INSERT INTO competitors (name, domain, website_url, category) VALUES
    ('SecureLayer7',   'securelayer7.net',  'https://www.securelayer7.net',  'Penetration Testing & ASV'),
    ('XM Cyber',       'xmcyber.com',       'https://xmcyber.com',           'Exposure Management'),
    ('AttackIQ',       'attackiq.com',      'https://www.attackiq.com',      'Breach & Attack Simulation'),
    ('Pentera',        'pentera.io',        'https://pentera.io',            'Automated Security Validation'),
    ('Cymulate',       'cymulate.com',      'https://cymulate.com',          'Continuous Security Validation'),
    ('Hive Pro',       'hivepro.com',       'https://www.hivepro.com',       'Threat Exposure Management'),
    ('Picus Security', 'picussecurity.com', 'https://www.picussecurity.com', 'Breach & Attack Simulation'),
    ('Ridge Security', 'ridgesecurity.ai',  'https://ridgesecurity.ai',      'Automated Penetration Testing')
ON CONFLICT (name) DO NOTHING;
