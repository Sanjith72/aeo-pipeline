# Running / Deploying AEO Studio

Three ways to run the full stack (backend API + web UI), from "one command on your machine"
to "hosted on a public URL". Pick the one that fits.

---

## A. One command, locally — **no Docker** (recommended for your machine)

You already have the Python venv, `web/node_modules`, and Postgres, so this needs no new
installs. From the repo root:

```powershell
.\scripts\run.ps1            # starts API (:8000) + web (:3000), opens the browser
.\scripts\run.ps1 -NoLlm     # deterministic + fast (skips the LLM)
.\scripts\stop.ps1           # stop both
```

It launches the API and the web UI each in their own window (so you see logs and can Ctrl+C),
waits for the UI, and opens **http://localhost:3000**.

- **No database needed** for *Plan a new site*, *Quick analysis*, and *Deliverables* — those are
  deterministic/in-memory.
- **Deep audit** needs Postgres: have it running and run `aeo migrate` once.
- First run only: create the venv + install if you haven't —
  `python -m venv .venv ; .\.venv\Scripts\pip install -e ".[api]"` and `npm --prefix web install`.

---

## B. One command, anywhere — **Docker Compose** (whole stack incl. Postgres)

Needs Docker Desktop installed. Brings up **db → migrations → API → web** together:

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (+ any LLM/API keys)
docker compose up -d --build  # build + start everything
# open http://localhost:3000
docker compose logs -f api    # tail the API
docker compose down           # stop (add -v to wipe the DB volume)
```

Optional add-ons (off by default): `--profile llm up -d ollama` (local LLM),
`--profile worker up -d worker` (crawl-queue drainer). The deep audit works out of the box —
migrations run automatically and the DB is bundled.

Images: backend = the repo `Dockerfile` (`pip install ".[api]"` + Chromium for crawling);
web = `web/Dockerfile` (Next.js standalone). `NEXT_PUBLIC_API_BASE` is baked into the web image
at build time, so it defaults to the browser-reachable `http://localhost:8000`.

---

## C. Hosted on a public URL — the **$0/month stack** (verified 2026-07)

This removes local running entirely, but it uses **your** cloud accounts — I can't create or
authenticate those for you. Every row below is a genuinely free, ongoing tier (no trial
credits), with its hard limits stated:

| Piece | Where ($0) | How / limits |
|---|---|---|
| **Postgres + pgvector** | **Supabase Free** | `supabase/README.md` has the full setup. Use the **session pooler** URL — `postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require` (the direct `db.<ref>` host is IPv6-only on free; query params now reach libpq). Limits: 500 MB DB, **pauses after 7 idle days** (manual un-pause), **no automated backups** — both covered by `.github/workflows/keepalive.yml` (8-hourly health ping + weekly `pg_dump` artifact). |
| **API** (FastAPI + Chromium) | **Hugging Face Docker Space, CPU Basic** (free, 2 vCPU / 16 GB) — the proven live pattern (`Sanjith12/aeo-api`, wrapper Dockerfile clones this repo via a `GH_TOKEN` build secret) | deploy the repo `Dockerfile`; set `DATABASE_URL`, `AEO__API__AUTH_KEY`, `AEO__LLM__PROVIDER=hybrid` + the LLM keys; boot with `start-api` (= `aeo migrate` + `aeo serve`, reads `$PORT`). Limits: disk is **ephemeral** (all state must live in Postgres — it does), sleeps after 48 h idle (the keepalive ping prevents it; the waking request may 503 — the web proxy retries). A Vercel function can't host this (persistent browser); 0.1-vCPU free tiers (Render/Koyeb) starve Chromium. Fly.io no longer has a usable free tier. |
| **Web** (Next.js) | **Vercel Hobby** (`aeo-studio` → aeo-studio-nine.vercel.app) | set **runtime** env `API_BASE_URL=https://<your-api-host>` and `API_KEY=<AEO__API__AUTH_KEY>` — the server-side proxy (`app/api/[...path]/route.ts`) injects the key; nothing secret ships to the browser. `web/vercel.json` adds security headers + pins the proxy's `maxDuration=300` (the Hobby hard max, needs Fluid Compute on — default for new projects). Caveat: Hobby is contractually **non-commercial**; if this becomes a revenue tool, move to Pro or to Cloudflare Workers (free tier allows commercial use). |
| **LLM (hybrid Gemini + Qwen)** | **Gemini AI Studio free tier** (key from a **no-billing** Google Cloud project) + **Groq free plan** (Qwen 3 32B, no card) + optional **OpenRouter `:free`** fallback key | `AEO__LLM__PROVIDER=hybrid`, keys per `.env.example`. Free-quota budget ≈ 250 flash + 1000 flash-lite + 1000 Groq + 50 OpenRouter requests/day — the router's retry + failover exists precisely to ride these limits. Free-tier caveat: Google may train on free-tier prompts/outputs. |
| **Scheduling / keep-warm / backup** | **GitHub Actions cron** (free minutes) | `.github/workflows/keepalive.yml` — set repo **variable** `KEEPALIVE_URL` (the API `/api/health` URL) and repo **secret** `BACKUP_DATABASE_URL` (the session-pooler URL). Replaces host-level cron (Vercel Hobby cron is once/day with jitter). |

When public, **turn on auth**: set `AEO__API__AUTH_KEY` on the API and the matching
`API_KEY` on the web host so the proxy sends the `X-API-Key` header on every `/api/*` call.
Set `AEO__API__RATE_LIMIT` too — startup validation warns when either is missing.

**What about Railway?** `railway.json` ships ready to deploy the same image
(build = Dockerfile, predeploy `aeo migrate`, start `aeo serve`, healthcheck `/api/health`)
— but Railway has **no ongoing free tier** (one-time $5 trial credit, then Hobby at
$5/month). It's the documented paid escape hatch if the HF Space free tier ever changes,
not part of the $0 stack.

---

## Environment reference

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection (deep audit + reports); query params like `?sslmode=require` are honored | `postgresql://aeo:aeo@localhost:5432/aeo` |
| `AEO__LLM__ENABLED` | use the LLM (else deterministic) | `true` |
| `AEO__LLM__PROVIDER` | `hybrid` (Gemini+Qwen router) \| `gemini` \| `qwen` \| `ollama` \| `cloud` | `ollama` |
| `AEO__LLM__GEMINI_API_KEY` | Gemini side of the hybrid router (AI Studio key, no-billing project) | — |
| `AEO__LLM__QWEN_API_KEY` | Qwen side (Groq free plan by default) | — |
| `AEO__LLM__QWEN_FALLBACK_API_KEY` | optional OpenRouter `:free` fallback | — |
| `AEO__AGENTS__MODE` | `react` (agentic loop) or `ladder` (fixed sequence) | `react` |
| `AEO__API__AUTH_KEY` | require `X-API-Key` on `/api/*` (set in any public deploy) | unset (open) |
| `API_BASE_URL` / `API_KEY` | (web host, runtime) backend URL + key for the server-side proxy | `http://localhost:8000` / unset |
