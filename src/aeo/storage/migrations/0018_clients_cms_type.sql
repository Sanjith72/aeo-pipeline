-- Detected publishing platform for a client's site ('wordpress' | 'shopify' | 'unknown').
--
-- Drives the "I'll do it myself" instructions in the implementation dashboard: the same
-- task gets WordPress- vs Shopify- vs CMS-agnostic copy-paste steps. Stored on `clients`
-- (a per-site fact, like `industry`) so both the owner dashboard and the read-only share
-- view render the right steps without re-crawling.
--
-- Additive + idempotent. NULL means "not detected yet" — the brief generator treats that
-- the same as 'unknown'.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS cms_type VARCHAR(32);
