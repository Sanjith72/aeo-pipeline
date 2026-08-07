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
web = `web/Dockerfile` (Next.js standalone).

**How the web image reaches the API.** The browser never calls the backend directly — every
`/api/*` request goes to the Next.js server-side proxy (`web/app/api/[...path]/route.ts`),
which forwards it and injects the `X-API-Key` header. So the two variables that matter are
read at **runtime**, not baked in:

| Variable | When | Set in compose as |
|---|---|---|
| `API_BASE_URL` | runtime | `environment:` → `http://api:8000` (the in-network service) |
| `API_KEY` | runtime | `environment:` → the same value as `AEO__API__AUTH_KEY` |
| `NEXT_PUBLIC_SUPABASE_URL` | **build** | `build.args:` — inlined by `next build` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | **build** | `build.args:` — inlined by `next build` |

The Supabase pair is the exception, and the reason `web/Dockerfile` takes build args at all:
`NEXT_PUBLIC_*` values are substituted into the bundle during `next build` and are **not**
read from the environment at runtime. Passing them as `environment:` does nothing — the image
ships with auth disabled (no Sign-in button, every pack gate open to the anonymous tier).
`docker-compose.yml` already passes each one in the right place; copy that split if you build
the image yourself.

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

**Check the safety net is real, not just configured.** Supabase Free has no automated
backups, so `keepalive.yml`'s weekly `pg_dump` is the only one — and a workflow that silently
stops producing artifacts looks identical to one that never ran. Verify the artifact exists
and has a plausible size (the job fails any dump under 4 KB, but nothing checks it kept
running):

```bash
gh run list --workflow=keepalive.yml -L 30                # pings every 8h, backup Mondays
gh api repos/<owner>/<repo>/actions/runs/<run-id>/artifacts \
  --jq '.artifacts[] | "\(.name) \(.size_in_bytes) bytes expired=\(.expired)"'
```

Artifacts expire after 90 days, so download anything you actually want to keep. Verified
2026-08-06: `KEEPALIVE_URL` and `BACKUP_DATABASE_URL` are both set, pings are green, and the
most recent dump is ~21 MB.

**Vercel env scopes — the thing that breaks every preview deployment.** Vercel scopes each
variable to Production / Preview / Development independently, and the "Production" default in
the UI is a trap: with `API_BASE_URL` and `API_KEY` set for Production only, every `/api/*`
call on a preview build falls back to `http://localhost:8000` *inside the serverless function*
and fails. Re-add both with **all three boxes ticked** (Settings → Environment Variables), and
do the same for the Supabase pair — those are build-time inlined, so they additionally need a
**redeploy**, not just a save. The proxy now detects this exact state and answers **503** with
the variable named, plus one server-side log line in Vercel's function logs, instead of the old
generic 502 that looked like a backend outage.

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

When it *is* 401, don't guess which of the four possible causes it is — ask:

```bash
python scripts/check_auth_config.py --token -      # then paste the token; stdin, not argv
```

This runs the token through the API's own verification path and names the check that
refused it: expired, issuer mismatch, wrong audience, bad signature, no matching `kid`, or
"valid but not an end-user". The last one is what you get if you paste the project's **anon**
key by mistake — it is a real JWT signed with the same secret, so it verifies and is then
correctly refused. The token, your email and every secret stay out of the output.

Copy the token from devtools → Application → Local Storage → `sb-<ref>-auth-token` →
`access_token`. They expire in about an hour, so use a fresh one.

### Verifying the P5 ownership backfill (migration 0034)

Migration 0034 stamps `owner_user_id` on pack ticket boards created before ownership was
threaded through the pipeline. It claims a board only where the evidence is unambiguous —
exactly one distinct user holds a live `pack`/`all_packs`/`tickets` grant on that domain —
and leaves anonymous and ambiguous boards unowned rather than guessing, because guessing
wrong locks the real owner out of their own board.

It runs on the factory rebuild (`aeo migrate` in `scripts/start-api.sh`). Confirm it
actually applied, rather than assuming:

```sql
-- 1. Did the migration record itself?
SELECT version, name, applied_at FROM schema_versions WHERE version = '0034';

-- 2. What did it do? (unowned boards are expected — anonymous ones stay open by design)
SELECT COUNT(DISTINCT client_id) FILTER (WHERE owner_user_id IS NOT NULL) AS owned,
       COUNT(DISTINCT client_id) FILTER (WHERE owner_user_id IS NULL)     AS still_unowned
  FROM implementation_milestones
 WHERE milestone_key LIKE 'pack:%';
```

A board left unowned is not a failure: it stays open until the first authenticated read or
mutation claims it. Zero rows from query 1 means the rebuild did **not** pick up the new
commit — a plain restart reuses the cached layer. Factory rebuild, then re-check.

### Is the Space running the code I think it is?

For a long time this had no answer. `/api/health` returned `{"status":"ok","db":"ok"}` whether
or not a rebuild had taken, and the Spaces API's `runtime.sha` is the **wrapper repo's**
commit — the wrapper clones this repo at build time, so its SHA moves for unrelated reasons
and stands still when this repo changes. A backend fix whose only signature is an internal SQL
clause was therefore unfalsifiable.

`/api/health` now reports a `build` field: a content hash of the installed `aeo` package
(`src/aeo/build.py`). It needs nothing from any dashboard and a stale layer cannot fake it —
if the running bytes differ, the hash differs.

```bash
aeo build-id                                              # the commit you believe you shipped
curl -s https://sanjith12-aeo-api.hf.space/api/health      # what is actually running
```

Equal → the rebuild took. Different → it did not, whatever the dashboard says. It is an
equality check, not a version: it does not order, and a bigger value means nothing.
`scripts/smoke_prod.py` does this comparison for you.

---

## D-bis. The deploy runbook — the exact order, every time

There is **no continuous deployment in this repo.** `.github/workflows/ci.yml` runs tests,
typechecks, and pushes an image to GHCR; it deploys nothing. `keepalive.yml` pings and backs
up. So a merged fix has changed nothing for your users until you walk this list.

### 0. Before you merge

```bash
pytest -q                        # backend
npm --prefix web run build       # web: this type-checks and lints internally
```

> `npm --prefix web run lint` does **not** work — there is no ESLint config in the repo, so
> it drops into an interactive setup prompt. `next build` is the real gate.

### 1. Note what you are shipping

```bash
aeo build-id        # e.g. a6cfd76bd371 — remember this, step 5 checks for it
```

### 2. Merge to `main` and push

Vercel picks this up on its own and redeploys the **frontend**. Nothing else does.

### 3. Migrations

`aeo migrate` runs at container boot via `scripts/start-api.sh`, so migrations apply during
step 4's rebuild — they do **not** apply on a merge. If this deploy added one, say so out
loud, and confirm it afterwards rather than assuming (step 6).

### 4. Backend: **FACTORY REBUILD** the Space — the step that gets forgotten

`Sanjith12/aeo-api` → Settings → **Factory rebuild**. A plain **Restart** reuses the cached
layer and keeps the OLD code while looking completely successful. This is how a fixed backend
bug survives into production.

### 5. Verify — against production, never localhost

```bash
# NOTE the interpreter: it must be one that can `import aeo` — i.e. the project venv
# (.venv/Scripts/python.exe on Windows), not a bare system python. A python without the
# package cannot compute this checkout's build id, so the build-id comparison does not run.
.venv/Scripts/python.exe scripts/smoke_prod.py \
    --base https://sanjith12-aeo-api.hf.space \
    --site https://aeo-studio-nine.vercel.app
```

A clean run reads **`8/9 passed, 1 SKIPPED`** — the skip being the opt-in overview check.
Always read the `DID NOT RUN` block: a skipped check proves nothing, and it is counted in the
denominator precisely so a partial run cannot read as a complete one.

It checks health + DB, that the deployed **build id matches the commit you shipped**, that
the service key is enforced, that `POST /api/entitlements/grant` is not reachable on the
Space's own URL, that the Stripe webhook rejects an unsigned POST with 400, that ticket
mutations need a key, and that the web origin's proxy actually reaches the backend. It is
read-only: no user, no charge, no entitlement, and the one expensive check (a fresh overview)
only runs with `--overview-domain`.

**If the build id does not match, the rebuild did not take.** Rebuild again — do not debug the
old code.

### 6. Confirm the migration actually applied

Against the production database (the `schema_versions` query under "Verifying the P5
ownership backfill" below shows the pattern). Zero rows for the version you expect means
step 4 did not pick up the new commit.

### 6-bis. When the rebuild FAILS — read this before you click it

**A failed factory rebuild takes production down.** The rebuild replaces the running
container; if the build errors, the Space lands in `stage: BUILD_ERROR` with *nothing*
serving, and there is no "redeploy the last good image" button. The only way back is a build
that succeeds. So a rebuild is not a safe no-op — budget for the site being down until the
build is green, and prefer not to click it just before you stop for the day.

**The most likely reason it fails has nothing to do with your code.** The Space's Dockerfile
clones this PRIVATE repo at build time with a `GH_TOKEN` BuildKit secret. GitHub PATs expire.
`--mount=type=secret,id=GH_TOKEN,required=true` guarantees the secret **exists**, not that it
is **valid** — so an expired token sails past `required=true` and dies in the clone:

```
remote: Invalid username or token. Password authentication is not supported for Git operations.
fatal: Authentication failed for 'https://github.com/<owner>/<repo>.git/'
--> ERROR: process "/bin/sh -c git clone ..." did not complete successfully: exit code: 128
```

Happened 2026-08-06, one month after the Space was created, and it cost a production outage.

**Do NOT diagnose this from the Space's `errorMessage`.** The HF API and the UI banner show a
buildkit CACHE-MISS report — every step listed as "cache miss", which is simply what a factory
rebuild does — followed by `Job failed with exit code: 1`. It names no failing step and reads
like a code problem. Read the real log instead (`huggingface_hub` is already a dependency):

```bash
python -c "from huggingface_hub import fetch_space_logs; \
  [print(l, end='') for l in fetch_space_logs('Sanjith12/aeo-api', build=True, token='hf_...')]"
```

The endpoint is `/api/spaces/<id>/logs/build`; it 401s anonymously, so it needs the **owner's**
HF token. The Space UI's Logs → Build → "View raw logs" shows the same thing.

If the trailing counter is all you have, it is decodable: BuildKit marks a FAILED vertex as
completed (`solver/jobs.go::notifyStarted` sets `Completed` unconditionally, then records
`Error`), so `user_completed:N` means N vertices finished and the last of them is the one that
failed — not "N succeeded". `user_completed:6` of 8 pointed at step 4, the clone.

**Fix:** mint a fresh token (fine-grained, Contents: Read-only on this repo, with a calendared
expiry), replace `GH_TOKEN` in Space Settings → Variables and secrets, and rebuild.

**Early warning:** `keepalive.yml`'s 8-hourly ping is what tells you the Space is down — it
went red within hours of the outage above. Treat a failed keepalive run as an outage alarm,
not as cron flakiness.

### 7. Rollback

```bash
git revert -m 1 <the merge commit>   # then push
```

Vercel redeploys the frontend automatically; the Space needs **another factory rebuild** to
go back. Migrations do **not** roll back — they are forward-only, so a revert that undoes
application code leaves the schema ahead. Check the reverted migration is additive
(a new table/column is harmless to leave) before relying on a revert alone. Env changes
revert by hand in whichever dashboard you changed.

---

## E. Payments (Stripe Checkout) — and the two ways to half-configure it

Pricing is a **flat price per pack**. Buying one grants exactly `scope='pack', pack_index=N`
— the same entitlement a promo code or a manual grant produces, so payment is a new *source*,
not a new access model. Leave it unconfigured and the buy path 503s while promo and manual
grants keep working; `GET /api/config` reports `payments_enabled:false` so the UI hides the
Buy button instead of offering one that fails.

**1. Stripe Dashboard → Developers → API keys** — copy the **Secret key**
(`sk_test_…` while testing, `sk_live_…` when real) → `AEO__PAYMENTS__STRIPE_SECRET_KEY`.

**2. Developers → Webhooks → Add endpoint.**

```
URL      https://<your-api-host>/api/webhooks/stripe
Events   checkout.session.completed
         checkout.session.async_payment_succeeded
```

**Both events, not one.** Cards settle instantly and arrive on the first. Delayed methods
(ACH / SEPA / Bacs) arrive **unpaid** on the first and only pay on the second — subscribe to
`completed` alone and those customers are charged and never granted their pack, silently.
Copy the endpoint's **signing secret** (`whsec_…`) → `AEO__PAYMENTS__WEBHOOK_SECRET`. It is
the only credential on that route, which is exempt from the `X-API-Key` guard because Stripe
cannot send one. The webhook is the **only** thing that turns money into an entitlement.

**3. `AEO__PAYMENTS__PUBLIC_APP_URL`** — the public **web app** origin, e.g.
`https://aeo-studio-nine.vercel.app`.

### The two half-configurations, and why both are fatal at boot

Neither of these looks broken from a dashboard, which is why startup validation
(`src/aeo/startup.py::_check_payments`) refuses to serve rather than warn:

| Half-configuration | What the customer experiences |
|---|---|
| Secret key set, **webhook secret missing** | Checkout succeeds and the card is charged. Every webhook delivery fails signature verification, so no entitlement is ever written, and Stripe gives up retrying after ~3 days. Money taken, nothing delivered, no error anywhere in your logs. |
| Payments on, **`PUBLIC_APP_URL` missing** | `create_pack_checkout` falls back to the request origin — which behind the Next.js proxy is the **backend's** host. Stripe returns the paying buyer to `<api-host>/studio`, a route the API does not serve. A 404 immediately after a successful charge. |

So the key and the webhook secret must be set **together**, and `PUBLIC_APP_URL` must be set
whenever payments are on. A Space missing any of them will now fail to boot with the reason
named in its startup log, instead of booting happily and selling something it cannot deliver.

### Test it end to end before going live

1. Use a **test-mode** secret key and the test endpoint's own `whsec_…`.
2. Buy a pack with card `4242 4242 4242 4242`, any future expiry, any CVC.
3. Locally, skip the public URL entirely:
   `stripe listen --forward-to localhost:8000/api/webhooks/stripe` — it prints a `whsec_…` of
   its own; use that as `WEBHOOK_SECRET` while testing.
4. Replay without paying again: `stripe trigger checkout.session.completed`.
5. Confirm the grant landed: the buyer returns to `/studio?checkout=success`, the studio polls
   entitlements until the (asynchronous) webhook grant appears, and the bought pack opens
   unlocked. A pack still locked after ~20s means the webhook never arrived — check
   **Stripe → Developers → Webhooks → the endpoint → Recent deliveries**, not the app.

---

## Environment reference

Cross-checked against `src/aeo/settings.py` and `.env.example`. "Required" means *required for
a public deployment* — the local dev defaults are deliberately permissive.

### Core

| Variable | Req? | Purpose | Default |
|---|---|---|---|
| `DATABASE_URL` | **yes** | Postgres connection (deep audit + reports); query params like `?sslmode=require` are honored. The Space's disk is ephemeral, so this is where ALL state lives | `postgresql://aeo:aeo@localhost:5432/aeo` |
| `AEO__LLM__ENABLED` | no | use the LLM (else deterministic) | `true` |
| `AEO__LLM__PROVIDER` | no | `hybrid` (Gemini+Qwen router) \| `gemini` \| `qwen` \| `ollama` \| `cloud` | `ollama` |
| `AEO__LLM__GEMINI_API_KEY` | for `hybrid` | Gemini side of the hybrid router (AI Studio key, no-billing project) | — |
| `AEO__LLM__QWEN_API_KEY` | for `hybrid` | Qwen side (Groq free plan by default) | — |
| `AEO__LLM__QWEN_FALLBACK_API_KEY` | no | optional OpenRouter `:free` fallback | — |
| `AEO__AGENTS__MODE` | no | `react` (agentic loop) or `ladder` (fixed sequence) | `react` |

### API surface

| Variable | Req? | Purpose | Default |
|---|---|---|---|
| `AEO__API__AUTH_KEY` | **yes** | require `X-API-Key` on `/api/*`. `aeo serve` refuses to boot without it (or `ALLOW_OPEN`) | unset → **fatal at boot** |
| `AEO__API__ADMIN_KEY` | **with `AUTH_KEY`** | `X-Admin-Key` for the entitlement-MINTING routes. Must differ from `AUTH_KEY` — admin routes 503 until you set it | unset (admin routes disabled) |
| `AEO__API__ALLOW_OPEN` | no | localhost-only escape hatch: permits serving with no `AUTH_KEY`. **Never set on a public host** | unset |
| `AEO__API__CORS_ORIGINS` | browser-direct only | comma-separated browser origins allowed to call the API. **Not needed in this topology** — see the note below | `http://localhost:3000,http://127.0.0.1:3000` |
| `AEO__API__RATE_LIMIT` / `AEO__API__RATE_WINDOW_SEC` | recommended | per-IP `/api/*` throttle. `0` disables it; startup **warns** on a public deploy without it | `0` / `60` |
| `AEO__API__OVERVIEW_DAILY_LIMIT` | recommended | fresh (non-cached) overview builds per IP per day — the expensive path. `0` disables | `0` |
| `AEO__API__OVERVIEW_GLOBAL_DAILY_LIMIT` | recommended | global ceiling on fresh overview builds; the backstop the per-IP cap cannot provide, since `X-Forwarded-For` is spoofable | `0` |

> **On CORS, correcting an earlier version of this table.** This row used to read
> Req? = **yes**, "must list your deployed web origin". That was wrong, and worth stating
> plainly because acting on it wastes time chasing a setting that changes nothing. The browser
> never calls the backend: every `/api/*` request goes to the Next.js **server-side proxy**,
> which forwards it from a server, and server-to-server requests send no `Origin` header and
> are not subject to CORS at all. Your deployed web origin does not belong in this list and
> adding it has no effect on the product.
>
> It matters only when a **browser is pointed straight at the API** — local development
> against `localhost:8000`, a second front end, or someone poking `/docs`. Left blank the CORS
> middleware is not installed at all, which is a perfectly good posture for a
> proxy-only deployment. Startup validation now warns about entries that can never match
> (a trailing slash or a path — a browser sends only `scheme://host[:port]`) and about `*`,
> rather than about the value being "wrong" for a deployment that does not use it.

### User auth (Supabase JWT) — a separate boundary from `AUTH_KEY`

| Variable | Req? | Purpose | Default |
|---|---|---|---|
| `AEO__AUTH__JWKS_URL` | one of the two | verify user tokens against the project's public keys — for **asymmetric** projects, the current Supabase default (`https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`) | unset |
| `AEO__AUTH__JWT_SECRET` | one of the two | verify with the **legacy shared secret** instead. Neither set → auth is open and nothing is gated | unset |
| `AEO__AUTH__JWT_ISSUER` | recommended | pin `https://<ref>.supabase.co/auth/v1` so a token minted for another project is refused. **No trailing slash** — the compare is exact and one slash 401s every login (fatal at boot) | unset |
| `AEO__AUTH__PROMO_CODES` | for promos | comma-separated codes redeeming to an `all_packs` grant. Empty → every code is a 422. Works with or without Stripe | `""` |

### Payments (Stripe) — see section E

| Variable | Req? | Purpose | Default |
|---|---|---|---|
| `AEO__PAYMENTS__STRIPE_SECRET_KEY` | for payments | `sk_live_…` / `sk_test_…`. Never `NEXT_PUBLIC_*` | unset (payments off) |
| `AEO__PAYMENTS__WEBHOOK_SECRET` | **with the key** | `whsec_…` from the endpoint. The only credential on `/api/webhooks/stripe`, and the only thing that turns money into an entitlement. Key without it → **fatal at boot** | unset |
| `AEO__PAYMENTS__PUBLIC_APP_URL` | **with payments** | the public WEB origin Stripe returns the buyer to. Unset while payments are on → **fatal at boot** (otherwise buyers land on the API's own 404) | unset |
| `AEO__PAYMENTS__PACK_PRICE_CENTS` | no | flat price for one pack, minor units (`4900` = $49.00). Must be > 0 unless a Price id is set | `4900` |
| `AEO__PAYMENTS__CURRENCY` | no | ISO currency for the inline price | `usd` |
| `AEO__PAYMENTS__STRIPE_PRICE_ID` | no | dashboard-managed Price instead of the inline amount (tax / multi-currency); overrides the two rows above | unset |
| `AEO__PAYMENTS__ENABLED` | no | the master switch. Set `false` to **stop selling while keeping the credentials configured** — during a pricing change, or after a fraud incident. Without it the only way to stop the buy path is to unset the secret key, which is indistinguishable from having lost it | `true` |
| `AEO__PAYMENTS__SUCCESS_PATH` | no | where Stripe returns a buyer, joined onto `PUBLIC_APP_URL`. The studio reads `?checkout=success` here; change it and the return leg stops being recognised | `/studio?checkout=success` |
| `AEO__PAYMENTS__CANCEL_PATH` | no | as above for an abandoned checkout | `/studio?checkout=cancelled` |
| `AEO__PAYMENTS__REQUEST_TIMEOUT_SEC` | no | how long to wait on Stripe's API before giving up. Worth raising on a cold free-tier Space, where the first outbound call after an idle period is slow | `20.0` |

### Web host (Vercel) — not in the backend `.env`

| Variable | Req? | When | Purpose |
|---|---|---|---|
| `API_BASE_URL` | **yes** | **runtime** | backend origin for the server-side proxy. Must be set in **Production + Preview + Development** — Production-only is why preview deployments cannot reach the API |
| `API_KEY` | **yes** | **runtime** | the value of `AEO__API__AUTH_KEY`; the proxy injects it as `X-API-Key`. Same three scopes |
| `NEXT_PUBLIC_SUPABASE_URL` | for auth | **build** | the browser auth client. Inlined by `next build` — changing it needs a **redeploy**, saving it does nothing |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | for auth | **build** | as above. Unset → no Sign-in button and everything stays anonymous |
