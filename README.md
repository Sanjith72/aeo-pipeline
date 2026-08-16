<div align="center">

# 🛰️ AEO Studio

### AI-first website improvement — audit, fix packs, and proof of improvement

Enter a URL, get a free five-skill overview of how AI search engines see the site, then unlock
prioritized **fix packs**, work them as a gamified quest, and verify the score actually moved.

[![CI](https://github.com/Sanjith72/aeo-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Sanjith72/aeo-pipeline/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Next.js](https://img.shields.io/badge/web-Next.js%20App%20Router-black.svg)](web/)
[![Tests: offline](https://img.shields.io/badge/tests-1100%2B%20offline-brightgreen.svg)](#testing)
[![Status: v5](https://img.shields.io/badge/status-v5-blue.svg)](docs/product/AEO_PRODUCT_CHANGES_v5.md)

</div>

---

## What it is

**AEO Studio** helps a business make its website legible to AI answer engines (ChatGPT,
Perplexity, Gemini, …) — and to the buyers who ask them. It started as a niche
Answer-Engine-Optimization crawler for one cybersecurity vertical; **v5 re-aimed the same
engine into a horizontal, self-serve product**:

1. **URL-first entry** — a URL is the only required input. `POST /api/overview` returns a free
   five-skill homepage overview (cached, rate-capped, SSRF-guarded) plus a preview of the fix packs.
2. **Deep audit** — crawls the site (discovery → prioritization → top-N pages), extracts
   structural signals, and scores every page on **five outcome skills**: *Messaging, Conversion,
   Discovery & Visibility, Proof & Trust, Structure & UX*. Scoring is deterministic-first; an LLM
   refines the Messaging/Conversion judgments on the paid deep audit only.
3. **Fix packs** — issues become ordered, bounded packs of tickets. Pack 1 is free; deeper packs
   unlock progressively behind **Supabase auth** (Google sign-in) and **Stripe payments**, with
   entitlements enforced server-side.
4. **Do the work, prove it moved** — each ticket carries how-to guidance; a **verified re-crawl**
   measures before/after per milestone, and a **gamified quest map** (phases, coins, achievements)
   tracks progress. Plans are shareable via tokenized links.

Under the hood the v4 engine is intact: the **Reference Architecture** blueprint + coverage diff,
non-circular recommendation validation, PostgreSQL as both job queue and result store, and an
**agent layer** (planner → builder → critic with human review) for generated fixes.

---

## Architecture at a glance

```
Browser ─► web/  Next.js (App Router, Tailwind)
              │   server-side /api/* proxy (injects the service key)
              ▼
         src/aeo/api  FastAPI  (aeo serve)
              │   auth: Supabase JWT (HS256 or JWKS) · entitlements · Stripe webhooks
              ▼
         src/aeo/…  crawl → extract → score(5 skills) → packs → recommend → validate → report
              │
              ▼
         PostgreSQL / Supabase   (job queue + results + auth + RLS)
```

- **Web** (`web/`): marketing page, `/overview` (free scan), `/studio` (the product), `/plan/[id]`
  (shareable plan), `/agents` (review queue). Auth degrades gracefully — without Supabase env vars
  there is no sign-in button and everything runs anonymous/open.
- **API** (`src/aeo/api/`): endpoints from [docs/product/PRODUCT_FLOW.md](docs/product/PRODUCT_FLOW.md) §3
  plus v5 overview/packs/entitlements/payments; contracts locked in
  [docs/V5_CONTRACTS.md](docs/V5_CONTRACTS.md).
- **Engine** (`src/aeo/`): the deterministic pipeline — same inputs, same numbers. The LLM is
  optional everywhere and disabled in tests.

---

## Quickstart

**One command, locally (Windows, no Docker):**

```powershell
.\scripts\run.ps1        # starts API (:8000) + web (:3000), opens the browser
.\scripts\stop.ps1       # stop both
```

**Docker Compose (whole stack incl. Postgres):**

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (+ any keys)
docker compose up -d --build  # db → migrate → api → web, then open http://localhost:3000
```

**Manual:**

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[api,dev]"
python -m playwright install chromium
cp .env.example .env            # set DATABASE_URL
aeo migrate
aeo serve                       # API on :8000, docs at /docs
# in another shell:
cd web && npm install && npm run dev   # UI on :3000
```

Full deploy story (hosted topology, env reference, runbooks): **[DEPLOY.md](DEPLOY.md)** and
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## CLI reference

The `aeo` CLI drives the engine directly (the web UI uses the same code through the API):

```text
# Serve & migrate
aeo migrate                  apply pending DB migrations
aeo serve                    run the FastAPI backend

# Audit pipeline
aeo audit  DOMAIN -t NAME    discover → prioritize → crawl → extract → score  (--dry-run: no DB)
aeo discover DOMAIN          discover + rank a site's URLs (no crawl, no DB)
aeo run    URLS… -t NAME     crawl → extract → score for explicit URLs
aeo crawl  URLS… -t NAME     crawl → extract only (score later)
aeo score  -r RUN_ID         score a run's extracted-but-unscored pages
aeo analyze -r RUN_ID        gap → recommend → validate (≤3 retries) → per-page report
aeo audit-cycle DOMAIN -t N  weekly loop: blueprint → coverage → crawl → analyze → site report

# Product / intelligence
aeo profile URL              classify a business: model, industry, journey gaps, action plan
aeo plan                     turn a business brief into a blueprint + strategy + action plan
aeo deliverables             generate the implementation bundle (sitemap, briefs, JSON-LD, …)
aeo agent                    run the agent layer (planner → builder → critic) for a ticket
aeo verify-milestones        before/after re-crawl proof for implementation milestones

# Reference Architecture (v4)
aeo blueprint generate|show  versioned ideal-site blueprint
aeo framework bootstrap      per-domain ideal-site framework
aeo coverage    -r RUN_ID    site-level coverage diff (missing / thin pages)
aeo site-report -r RUN_ID    render the site-level AEO report
aeo refinements [--propose]  validated-wins criteria-target proposals (human-gated)

# Ops
aeo enqueue URLS… -t NAME    queue a crawl batch · aeo worker  drain the queue
aeo status [-r RUN_ID]       DB health, queue depth, run report
aeo trace  PAGE_ID           dump a page's agent journey (observability)
aeo report TARGET            render per-page reports · aeo onboard / targets / add-target
```

---

## Configuration

Settings are layered: **defaults → `config/*.yaml` → environment** (`AEO__SECTION__KEY`).

| File | Purpose |
|------|---------|
| [`config/scoring.yaml`](config/scoring.yaml) | The rubric + the v5 `skills:` block (weights, impact-ranked priorities). |
| [`config/framework.yaml`](config/framework.yaml) | Curated framework for the Reference Architecture. |
| [`config/crawler.yaml`](config/crawler.yaml) | Crawl politeness, retries, fingerprinting. |
| [`config/prioritization.yaml`](config/prioritization.yaml) | Page-value ranking for top-N selection. |
| [`config/intelligence.yaml`](config/intelligence.yaml) | Business-intelligence layer (profile/plan) tuning. |
| [`config/extractors.yaml`](config/extractors.yaml) · [`config/entities.yaml`](config/entities.yaml) · [`config/best_practices.yaml`](config/best_practices.yaml) | Extractor tuning, entity lists, best-practice targets. |
| `config/domains/<domain>.yaml` | Per-client onboarding: topic, engine target, max URLs, label. |

Key environment groups (full reference in [DEPLOY.md](DEPLOY.md)): `DATABASE_URL`,
`AEO__AUTH__*` (Supabase JWT secret or JWKS URL), `AEO__PAYMENTS__*` (Stripe),
`NEXT_PUBLIC_SUPABASE_*` (web), `AEO__OBS__OTEL_ENABLED` (OpenTelemetry).
Optional dependency groups: `.[api]`, `.[otel]`, `.[pdf]`, `.[embeddings]`, `.[dev]`.

---

## Project structure

```text
src/aeo/
  cli.py · settings.py     typer CLI (`aeo`) · layered config
  api/                     FastAPI app, Supabase-JWT auth, job endpoints
  crawl/                   discovery, Crawl4AI wrapper, politeness, retry, prioritization
  extract/                 pure extractors (meta, schema, qa, stats, …)
  scoring/                 rubric + the 5-skill layer (scorers/, skills.py)
  intelligence/            business profiling: intake, classification, journey, site facts
  pipeline/                orchestrator, worker, overview, packs, milestone audit
  reference/               blueprints, frameworks, competitor discovery, onboarding
  processor/ · recommender/ · validation/   gap analysis → recommendations → non-circular checks
  agents/                  planner → builder → critic runtime (+ ReAct loop, tools)
  entitlements/ · payments/  pack unlock logic · Stripe integration
  companion/               gamification rewards (coins, achievements)
  report/ · obs/ · nlp/    deliverable reports · tracing/OTel/error sink · LLM providers
  storage/                 psycopg2 pool, 34 SQL migrations, repos/
web/                       Next.js app: overview, studio, quest map, plan sharing, agents
supabase/                  Supabase baseline migration (auth + RLS)
config/                    scoring · skills · crawler · intelligence · domains/ …
tests/                     unit + integration, fully offline by default
docs/                      see docs/README.md for the map (product/ · architecture/ · archive/)
ops/                       systemd timers + weekly audit/verify scripts
Dockerfile · docker-compose.yml · railway.json · .github/workflows/
```

---

## Testing

```bash
pytest -q          # 1,100+ tests, offline by default (no DB, browser, or LLM)
ruff check src tests
cd web && npm test          # web unit tests (node --test over lib/**/*.test.ts)
```

Integration tests that need Postgres skip cleanly when `DATABASE_URL` is unreachable — CI runs
without a database on purpose. Coverage and benchmark details: [docs/VALIDATION.md](docs/VALIDATION.md).

---

## Documentation

Start at **[docs/README.md](docs/README.md)** — the full map. Highlights:

| Doc | Contents |
|-----|----------|
| [docs/product/AEO_PRODUCT_CHANGES_v5.md](docs/product/AEO_PRODUCT_CHANGES_v5.md) | The authoritative v5 build spec (all phases shipped). |
| [docs/V5_CONTRACTS.md](docs/V5_CONTRACTS.md) | Locked JSON/DB contracts: skills, packs, entitlements. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Engine design rationale, module map, data flow. |
| [DEPLOY.md](DEPLOY.md) | Running + deploying the full stack; environment reference; runbooks. |
| [docs/architecture/](docs/architecture/) | Frozen v3/v4 architecture specs. |
| [docs/archive/](docs/archive/) | Historical plans, checklists, and release notes (provenance only). |

---

## Requirements

**Python ≥ 3.11** · **Node 20+** (web) · **PostgreSQL ≥ 14** (or Supabase) ·
**Playwright Chromium** · *(optional)* Ollama or a cloud LLM key, Stripe keys, PageSpeed API key.

---

## License

Internal project — all rights reserved. Not for external distribution without permission.
