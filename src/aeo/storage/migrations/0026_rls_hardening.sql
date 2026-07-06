-- src/aeo/storage/migrations/0026_rls_hardening.sql
-- Row Level Security hardening for hosted Postgres (Supabase).
--
-- The app connects as the table OWNER (postgres on Supabase, aeo locally), and owners
-- bypass non-FORCE RLS — so enabling RLS changes nothing for the app itself. What it
-- does close is Supabase's auto-generated Data API (PostgREST): the anon/authenticated
-- roles get RLS with NO policies (deny-all) and their blanket grants revoked, so the
-- database is reachable only through this backend. On a plain local Postgres the role
-- branches are skipped and enabling RLS is a harmless no-op for the owner.
--
-- NOTE for future migrations: new tables must ALTER TABLE ... ENABLE ROW LEVEL SECURITY
-- themselves (this migration only covers tables that exist when it runs).
--
-- DEPLOY CONSTRAINT: DATABASE_URL must connect as the table owner (or a BYPASSRLS
-- role). A least-privilege non-owner app role would be denied by the enable-all +
-- no-policies combination below.

DO $$
DECLARE
    t RECORD;
BEGIN
    -- Ownership-filtered: only ALTER tables this role can actually alter, so a stray
    -- extension-owned table in public (e.g. postgis) can't abort the migration.
    FOR t IN
        SELECT c.relname AS tablename
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND pg_has_role(c.relowner, 'USAGE')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM authenticated';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM authenticated';
    END IF;
END $$;
