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
Set `AEO__API__RATE_LIMIT` too — startup validation warns when it is missing.

`aeo serve` now **refuses to boot** without `AEO__API__AUTH_KEY` (a fatal startup error).
With neither it nor `AEO__API__ADMIN_KEY` configured, `require_admin_key` has nothing to
check and `POST /api/entitlements/grant` is completely ungated — anyone who can reach the
backend's own URL grants themselves every pack. (The proxy denylist in
`web/app/api/[...path]/route.ts` only covers requests routed through Vercel; the Space's
public URL bypasses it entirely.) For a localhost-only dev server, name the exception:
`AEO__API__ALLOW_OPEN=1` — which `scripts/run.ps1` and `docker-compose.yml` set for you.

**Set the two keys together.** This is the second-order trap that makes a deploy look
healthy while a feature is silently dead: once `AEO__API__AUTH_KEY` is set, the admin routes
return **503 until `AEO__API__ADMIN_KEY` is set as well**, because the service key is not an
authorization boundary — the web proxy hands it to every visitor's browser, so it
authenticates the *proxy*, not the person. Symptom of getting this half-right: manual and
promo grants fail with a 503 that mentions nothing about payments. Never reuse the same
value for both. Startup logs a warning naming this exact pairing.

**What about Railway?** `railway.json` ships ready to deploy the same image
(build = Dockerfile, predeploy `aeo migrate`, start `aeo serve`, healthcheck `/api/health`)
— but Railway has **no ongoing free tier** (one-time $5 trial credit, then Hobby at
$5/month). It's the documented paid escape hatch if the HF Space free tier ever changes,
not part of the $0 stack.

---

## D. Sign-in with Google (Supabase auth) — the part that has no file in this repo

Four things must agree. Three live in a dashboard, so **nothing in this repo can be wrong and
sign-in still fail**. That is exactly what happened once already: every env var was set
correctly and Google sign-in silently did nothing, because of step 3.

**1. Google Cloud** — APIs & Services → Credentials → OAuth 2.0 Client ID (Web application).
Under **Authorized redirect URIs** add exactly:

```
https://<project-ref>.supabase.co/auth/v1/callback
```

Note this is a **Supabase** URL, not your site's. Publish the OAuth consent screen
(Testing → In production); a screen left in *Testing* with 0 test users blocks everyone,
with an "access blocked" error that reads like a code bug.

**2. Supabase → Authentication → Providers → Google** — enable it, paste the client ID and
client secret from step 1.

**3. Supabase → Authentication → URL Configuration — the step that gets missed.**

```
Site URL       https://<your-deployed-origin>
Redirect URLs  https://<your-deployed-origin>/**
               http://localhost:3000/**          ← keep for local dev
```

GoTrue validates the `redirect_to` it was handed against **Redirect URLs**. If your deployed
`/auth/callback` is not in that list it does **not** error — it silently discards the value
and sends the user to **Site URL** instead, which defaults to `http://localhost:3000`. So
after consenting on Google the user lands on a dead localhost address (or, worse, gets
signed in on their *local dev server*), the production tab never receives a session, and
there is no error anywhere: no console message, no failed request, no server log. It just
looks like the button does nothing.

**4. Backend token verification.** Check the project's JWKS endpoint first:

```
https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json
```

* Returns **keys** → the project uses asymmetric JWT signing keys. Set
  `AEO__AUTH__JWKS_URL` to that URL. There is no shared secret to configure, and
  `AEO__AUTH__JWT_SECRET` will not exist in the dashboard.
* Returns **`{"keys":[]}`** → legacy shared-secret project. Set `AEO__AUTH__JWT_SECRET`
  (Settings → API → JWT Secret) instead; a JWKS URL would verify nothing.

**5. The frontend half.** `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY` on
Vercel. These are **inlined at build time**, so setting them changes nothing until you
redeploy — and the env-var UI showing them set is not evidence. Grep the deployed bundle for
the project ref; its absence is the proof.

### Verify it, don't assume it

```bash
python scripts/check_auth_config.py --live --site https://<your-deployed-origin>
```

`--live` probes the running project: Google enabled, JWKS resolves and carries an algorithm
the backend accepts, and — the one that catches step 3 — whether
`<your-deployed-origin>/auth/callback` is really in the Redirect URLs allowlist. It works by
asking GoTrue to bounce a deliberately **invalid** token and reading the `Location` header:
an allowlisted URL comes back verbatim, a non-allowlisted one comes back as your Site URL.
Read-only — no user, session or email is ever created. When the project's env lives on
Vercel rather than in `web/.env.local`, pass `--supabase-url` / `--anon-key`.

The one thing no probe can confirm is that the backend accepts a *real* user token — that
needs an actual sign-in. After step 3, sign in once and check the browser calls
`/api/auth/me` and gets **200**, not 401. A 401 there means step 4 is wrong.

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
| `AEO__API__AUTH_KEY` | require `X-API-Key` on `/api/*`. **Required to serve** — `aeo serve` refuses to boot without it (or `ALLOW_OPEN`) | unset → **fatal at boot** |
| `AEO__API__ADMIN_KEY` | `X-Admin-Key` for the entitlement-MINTING routes. Must differ from `AUTH_KEY`. **Set it whenever you set `AUTH_KEY`** — admin routes 503 until you do | unset (admin routes disabled) |
| `AEO__API__ALLOW_OPEN` | localhost-only escape hatch: permits serving with no `AUTH_KEY`. **Never set on a public host** | unset |
| `API_BASE_URL` / `API_KEY` | (web host, runtime) backend URL + key for the server-side proxy | `http://localhost:8000` / unset |
| `AEO__AUTH__JWKS_URL` | verify Supabase user tokens against the project's public keys — for projects using **asymmetric** JWT signing keys (`https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`) | unset |
| `AEO__AUTH__JWT_SECRET` | verify user tokens with the **legacy shared secret** instead. Set this *or* `JWKS_URL`, not usually both. Neither → auth is open and nothing is gated | unset |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | (web host, **build time**) the browser auth client. Unset → no Sign-in button renders and everything stays anonymous. Changing them requires a **redeploy** | unset |
