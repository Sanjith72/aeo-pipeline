# Deploy: Backend on Railway + Frontend on Vercel

**Why split?** The backend crawls with a real headless Chromium (Crawl4AI/Playwright) and runs
multi-minute deep audits with in-memory job state — none of which fits Vercel's stateless
serverless model. So: **FastAPI + crawler → Railway** (container, your `Dockerfile`), **Next.js
UI → Vercel**, **Postgres → Railway's managed plugin**. The UI talks to the Railway API over HTTPS.

> Nothing here touches your local `docker compose` stack — it keeps running independently.

---

## Part 1 — Backend (FastAPI + crawler) on Railway

1. **Create the project & service**
   - railway.app → **New Project** → **Deploy from GitHub repo** → pick `Sanjith72/aeo-pipeline`, branch `main`.
   - Railway reads [`railway.json`](../railway.json) → builds the root `Dockerfile`, runs `aeo migrate`
     (pre-deploy), then `aeo serve --host 0.0.0.0` on the injected `$PORT`, health-checked at `/api/health`.

2. **Add Postgres** — in the project: **New → Database → PostgreSQL**. It exposes a `DATABASE_URL`.

3. **Set the API service's variables** (Service → **Variables**):
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (Railway reference to the DB service) |
   | `AEO__LLM__ENABLED` | `true` |
   | `AEO__LLM__PROVIDER` | `cloud` |
   | `AEO__LLM__CLOUD_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` |
   | `AEO__LLM__CLOUD_MODEL` | `gemini-2.5-flash` |
   | `AEO__LLM__CLOUD_API_KEY` | *(your Gemini key)* |
   | `AEO__API__AUTH_KEY` | *(generate a long random string — gates every `/api/*` route)* |
   | `AEO__API__CORS_ORIGINS` | `https://<your-vercel-app>.vercel.app` *(fill in after Part 2)* |
   | `AEO__API__RATE_LIMIT` | `120` *(per-IP requests/min; `/api/health` exempt. 0 = off)* |

4. **Deploy** → Railway gives a public URL like `https://aeo-pipeline-production.up.railway.app`.
   Verify: open `…/api/health` → `{"status":"ok","db":"ok"}`. (Migrations ran via `preDeployCommand`;
   if your Railway plan doesn't support it, run once: `railway run aeo migrate`.)

---

## Part 2 — Frontend (Next.js) on Vercel

1. vercel.com → **Add New → Project** → import `Sanjith72/aeo-pipeline`.
2. **Root Directory → `web`** (important — the app lives in `web/`, not the repo root).
3. **Environment Variables** (Production) — **server-side only** (NOT `NEXT_PUBLIC_*`, so the
   key never reaches the browser; the app's `app/api/[...path]` proxy reads them at request time):
   | Variable | Value |
   |---|---|
   | `API_BASE_URL` | your Railway URL, e.g. `https://aeo-pipeline-production.up.railway.app` |
   | `API_KEY` | the **same** value as `AEO__API__AUTH_KEY` |

   > These are **runtime** vars (read by the server proxy on each request), so you can change them
   > and redeploy without a rebuild. The browser only ever talks to the Vercel app itself.
4. **Deploy** → you get `https://<app>.vercel.app`.
5. **Close the loop:** back on Railway, set `AEO__API__CORS_ORIGINS` to that exact Vercel URL and redeploy
   the API (CORS must allow the browser origin or every fetch fails silently).

---

## ✅ Security — the key is server-side

- **The API key never reaches the browser.** All calls go through a same-origin server proxy
  (`web/app/api/[...path]/route.ts`) that injects `X-API-Key` from the **server-only** `API_KEY` var.
  The old browser-visible-key problem is gone — set `AEO__API__AUTH_KEY` on Railway and the matching
  `API_KEY` on Vercel, and every `/api/*` route is genuinely gated.
- **Per-IP rate limiting is built in** — set `AEO__API__RATE_LIMIT` (e.g. `120` req/min/IP; `/api/health`
  exempt). It reads the client IP from `X-Forwarded-For`, so it sees the real browser IP through the
  Vercel→Railway proxy chain. Combined with the SSRF guard + the `_MAX_CONCURRENT_AUDITS` cap, the
  abuse/cost blast radius is bounded. (It's in-memory/single-process; a multi-replica API would want a
  shared store like Redis — not needed at this scale.)
- Keep `AEO__API__AUTH_KEY` set and watch usage.

## Cost / ops notes
- The backend image is large (bundled Chromium) and audits are CPU-heavy — Railway bills by usage; watch the meter.
- Gemini stays on the **free tier** (rate-limited). No ollama in production — it's local-only.
- After a code change, Railway redeploys on `git push` to the connected branch; Vercel redeploys on push too.
- A managed Postgres on Railway persists; back it up if it holds anything you care about.
