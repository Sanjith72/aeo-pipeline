# Supabase setup (free tier)

The database schema's source of truth is `src/aeo/storage/migrations/*.sql`, applied by
`aeo migrate`. This directory holds the **generated** Supabase-CLI mirror of that chain
(regenerate with `python scripts/export_supabase_baseline.py` whenever a migration lands).

## Option 1 — zero-step (recommended)

Create a Supabase project, copy the **Supavisor pooler** connection string, and set it as
`DATABASE_URL` on the API host. The API container boots with `start-api`, which runs
`aeo migrate` before serving — the schema applies itself over that connection.

## Option 2 — Supabase-CLI-first

```bash
supabase link --project-ref <your-project-ref>
supabase db push          # applies supabase/migrations/*_aeo_baseline.sql
```

The baseline also seeds `schema_versions`, so the app's own `aeo migrate` at boot sees
everything as applied and no-ops. Both options converge on the same schema — but **pick
one driver and stay with it**: alternating between `db push` and app-run migrations
desyncs the Supabase CLI's own migration-history table.

## Connection string rules (they matter)

- **Use the pooler host** (`aws-*-*.pooler.supabase.com`), not the direct
  `db.<ref>.supabase.co` host — the direct host is IPv6-only on the free plan and most
  container hosts (HF Spaces, Render, …) have no outbound IPv6.
- **Use SESSION mode — port 5432 on the pooler.** This app runs its own
  `ThreadedConnectionPool` (psycopg2), and stacking an app-side pool on the
  transaction-mode pooler (port 6543) is the classic footgun. Session mode gives each
  pooled connection normal Postgres semantics (DDL in `aeo migrate` included).
- **The pooler username is `postgres.<project-ref>`** (dot-suffixed), not plain
  `postgres`:
  `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`
- Append `?sslmode=require`. The app passes the URL to libpq verbatim, so query params
  are honored (startup validation warns if this is missing on a hosted URL).

## What the migrations set up

- The full application schema (26 migrations: crawl runs, rubric scores, gap analyses,
  recommendations, blueprints, milestones, agent runs, gamification, …).
- **pgvector** + `content_embeddings` (migration 0025) — created only where the `vector`
  extension is available (always true on Supabase); semantic search degrades gracefully
  elsewhere.
- **RLS hardening** (migration 0026) — every table gets RLS enabled with **no** policies
  for `anon`/`authenticated`, and their default grants revoked. Result: Supabase's
  auto-generated Data API exposes nothing; the only path to the data is this backend
  (which connects as the table owner and is unaffected). Constraint: `DATABASE_URL`
  must connect as the table **owner** (the default `postgres.<ref>` user is) or a
  BYPASSRLS role — a least-privilege non-owner app role would be denied by deny-all RLS.

## Free-tier notes

- Free projects **pause after 7 idle days**, and un-pausing is a manual dashboard action.
  `.github/workflows/keepalive.yml` pings the API's `/api/health` (which runs `SELECT 1`)
  every 8 hours — one request keeps both the free HF Space awake and Supabase active.
- **No automated backups on the free plan.** The same workflow uploads a weekly
  `pg_dump` as a GitHub artifact (90-day retention) — set the `BACKUP_DATABASE_URL`
  repo secret to enable it.
- 500 MB database cap — this schema stores page HTML (`crawled_pages.raw_html`), so a
  long-running install should prune old runs (`crawl_runs` cascade-deletes its pages).
- Two active projects per org — a staging project consumes the second slot.
