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

## C. Hosted on a public URL (cloud)

This removes local running entirely, but it uses **your** cloud accounts — I can't create or
authenticate those for you. Recommended split (all container-friendly):

| Piece | Where | How |
|---|---|---|
| **Postgres** | Neon / Supabase / RDS | create a DB, grab its `DATABASE_URL` (the app strips URL query params like `?sslmode=` — set `PGSSLMODE=require` as an env var instead; Neon's `-pooler` host works) |
| **API** (FastAPI + Chromium) | a container host with **≥ ~1 vCPU** — e.g. a Hugging Face Docker Space (free, 2 vCPU), Railway, or Fly.io. A Vercel serverless function won't fit (persistent browser, threads, Postgres), and 0.1-vCPU free tiers (Render/Koyeb free) starve Chromium *and* the health check, so the instance gets killed mid-audit | deploy the repo `Dockerfile`; set `DATABASE_URL`, `AEO__API__AUTH_KEY`, and (optional) `AEO__LLM__*`; boot with `start-api` (= `aeo migrate` + `aeo serve`, reads `$PORT`) |
| **Web** (Next.js) | **Vercel** (or the same container host via `web/Dockerfile`) | set **runtime** env `API_BASE_URL=https://<your-api-host>` and `API_KEY=<AEO__API__AUTH_KEY>` — the server-side proxy (`app/api/[...path]/route.ts`) injects the key; nothing secret ships to the browser. (`NEXT_PUBLIC_API_BASE`/`NEXT_PUBLIC_API_KEY` are legacy — no code reads them.) |

When public, **turn on auth**: set `AEO__API__AUTH_KEY` on the API and the matching
`API_KEY` on the web host so the proxy sends the `X-API-Key` header on every `/api/*` call.

Reference deploy (live): Neon (`aeo-pipeline` project) + HF Space `Sanjith12/aeo-api`
(public wrapper Dockerfile clones this private repo via a `GH_TOKEN` build secret) +
Vercel project `aeo-studio` → https://aeo-studio-nine.vercel.app

If you tell me which hosts you want (e.g. "Vercel + Render + Neon") and grant access, I'll wire
up the exact configs/CI for that path.

---

## Environment reference

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection (deep audit + reports) | `postgresql://aeo:aeo@localhost:5432/aeo` |
| `AEO__LLM__ENABLED` | use the LLM (else deterministic) | `true` |
| `AEO__LLM__PROVIDER` | `ollama` (local) or `cloud` (OpenAI-compatible) | `ollama` |
| `AEO__LLM__CLOUD_API_KEY` / `AEO__LLM__CLOUD_MODEL` | cloud LLM (faster/better than local qwen2.5:3b) | — |
| `AEO__API__AUTH_KEY` | require `X-API-Key` on `/api/*` (set in any public deploy) | unset (open) |
| `NEXT_PUBLIC_API_BASE` | (web build) URL the browser calls | `http://localhost:8000` |
| `NEXT_PUBLIC_API_KEY` | (web build) key sent as `X-API-Key` | unset |
