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
   - Railway reads [`railway.toml`](../railway.toml) → builds the root `Dockerfile`, runs `aeo migrate`
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

4. **Deploy** → Railway gives a public URL like `https://aeo-pipeline-production.up.railway.app`.
   Verify: open `…/api/health` → `{"status":"ok","db":"ok"}`. (Migrations ran via `preDeployCommand`;
   if your Railway plan doesn't support it, run once: `railway run aeo migrate`.)

---

## Part 2 — Frontend (Next.js) on Vercel

1. vercel.com → **Add New → Project** → import `Sanjith72/aeo-pipeline`.
2. **Root Directory → `web`** (important — the app lives in `web/`, not the repo root).
3. **Environment Variables** (Production):
   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE` | your Railway URL, e.g. `https://aeo-pipeline-production.up.railway.app` |
   | `NEXT_PUBLIC_API_KEY` | the **same** value as `AEO__API__AUTH_KEY` |

   > `NEXT_PUBLIC_*` are baked at **build time** — set them before the first build (or redeploy after changing).
4. **Deploy** → you get `https://<app>.vercel.app`.
5. **Close the loop:** back on Railway, set `AEO__API__CORS_ORIGINS` to that exact Vercel URL and redeploy
   the API (CORS must allow the browser origin or every fetch fails silently).

---

## ⚠️ Security — read before going public

- **`NEXT_PUBLIC_API_KEY` is in the browser bundle.** It's a *filter*, not real auth — anyone viewing the
  site can read it from the JS and call the API. Combined with the SSRF guard + the `_MAX_CONCURRENT_AUDITS`
  cap, it's an OK baseline for a demo, but **not** a hardened public service.
- **Proper fix (follow-up):** proxy API calls through the Next.js server (Route Handlers) so the key stays
  server-side and never reaches the browser; add per-IP rate limiting in front of the API.
- The audit endpoint makes your server crawl arbitrary URLs and spend your Gemini quota — keep `AEO__API__AUTH_KEY`
  set and watch usage.

## Cost / ops notes
- The backend image is large (bundled Chromium) and audits are CPU-heavy — Railway bills by usage; watch the meter.
- Gemini stays on the **free tier** (rate-limited). No ollama in production — it's local-only.
- After a code change, Railway redeploys on `git push` to the connected branch; Vercel redeploys on push too.
- A managed Postgres on Railway persists; back it up if it holds anything you care about.
