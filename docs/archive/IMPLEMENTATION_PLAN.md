# Implementation Plan — Outcome-Driven AEO Pipeline

The redesign is decomposed into four sub-projects, each its own spec → build → verify
cycle. Build order is dependency-driven: **the brain (SP-1) first, then the deliverables
(SP-2/SP-3), then the UI (SP-4)** that presents them. All sub-projects preserve the
deterministic-first contract and the validated v4 engine (reframe + extend, not rebuild).

```
SP-1 Intelligence Layer  ──►  SP-2 No-Website Entry  ──►  SP-4 Guided UI (FastAPI + React/Next)
        (DONE)                 SP-3 Asset Packager   ──►
```

---

## SP-1 · Backend Intelligence Layer — ✅ DONE (this turn)

**Scope:** classify the site, infer the business model, find journey gaps, route to a
scenario, and emit a prioritized strategy — deterministic-first, no DB migration.

**Delivered**
- `src/aeo/intelligence/`: `signals.py`, `config.py`, `classification.py`,
  `business_intent.py`, `journey.py`, `scenario.py`, `site_profile.py`, `__init__.py`.
- `config/intelligence.yaml` (tunable thresholds / signals / maps).
- 4 wiring seams: `dry_run` output; `coverage_diffs.detail["site_profile"]` persistence;
  site report `STRATEGY` section + render block; new `aeo profile DOMAIN` CLI.
- 7 test modules; design spec `docs/superpowers/specs/2026-06-09-aeo-intelligence-layer-design.md`.

**Verification:** 498 unit tests pass (+53 new), ruff clean, mypy clean; live
`aeo profile example.com` routes `single_page` end-to-end; adversarial multi-lens review
(deterministic contract holds; correctness/robustness findings fixed).

**Acceptance criteria — met:** every `SiteClass` routes to a scenario + deliverable;
single-page site yields a restructuring plan (not a bad score); LLM never gates; no
migration; existing suite unaffected.

---

## SP-2 · No-Website / Business-Input Entry Path — ✅ DONE

**Goal:** serve Scenario 1 (no website) end-to-end — turn a business brief into a packaged
AEO Website Blueprint with **no crawl**.

**Delivered**
- `reference/business_input.py` — `BusinessInput` brief (name, optional domain, category,
  topic, location, services[], competitors[], goals[]) with a stable `key()` (domain or
  name-slug) and `topic_hint()`.
- `intelligence/brief.py` — `BriefPlan` + `plan_from_brief(brief, framework, llm)`: builds
  the blueprint from the brief-tailored framework, diffs against an empty site (every ideal
  page missing), routes through the SP-1 layer to `no_website` + a prioritized build plan.
  Pure given the framework; `category` is plumbed into the business-model classifier
  (closing the `industry_hints` gap the SP-1 review flagged).
- `reference/framework.py` — extracted `build_framework(raw)` so a brief-tailored framework
  is usable **in-memory** (no file write required).
- CLI `aeo plan NAME [--domain] [--category] [--service …] [--competitor …] [--goal …]
  [--llm] [--write-config] [--json]` — read-only by default; `--write-config` persists the
  framework as the onboarding artifact.

**Verification:** 503 unit tests pass (+5 new `test_brief.py`), ruff clean, mypy clean on
new modules. Live `aeo plan "Acme Security" --domain acme.com --category cybersecurity
--no-llm` → `no_website` → AEO Website Blueprint (9-page ideal sitemap + build plan).

**Reuses:** `reference/framework_bootstrap.py`, `reference/generator.py`,
`processor/coverage_diff.py`, the SP-1 router (handles `SiteClass.NONE`).

**Acceptance — met:** a brief (even domain-less) produces a full blueprint + `no_website`
strategy with no live-site dependency; `category` measurably shifts the business model.
*(Live competitor discovery + entity merge from the brief — already in `onboard.py` — can
be wired into `aeo plan` as a thin follow-up if desired.)*

---

## SP-3 · Implementation Asset Packager — ✅ DONE

**Goal:** turn the blueprint + strategy into a **developer-ready bundle** the user can
implement directly.

**Delivered**
- `report/packager.py` — `Asset` / `AssetBundle` + `build_asset_bundle(blueprint, coverage,
  profile, origin, llm, draft_limit)` (pure) and `AssetBundle.write(out_dir)` (the only I/O).
  Assets: `README.md`, `sitemap.xml` (valid XML, absolute URLs), `navigation.md`
  (primary nav + cluster hierarchy), `content-briefs.md` (priority-ordered, every page),
  `internal-linking.md`, `schema-and-entities.md`, `STRATEGY.md` (when a profile is given),
  and `pages/<slug>.md` per-page spec sheets (H1 + sections + FAQ + **code-built JSON-LD**,
  reusing `recommender/draft.py`), + a `manifest.json`.
- CLI: `aeo plan … --bundle DIR` (no-website path, in-memory) and `aeo deliverables -r
  RUN_ID --out DIR` (DB-backed: pinned blueprint + coverage diff + embedded SP-1 profile).

**Verification:** 509 unit tests pass (+6 `test_packager.py`), ruff clean, mypy clean on
the new module. Live `aeo plan "Acme Security" … --bundle <dir>` wrote a **17-file** bundle
(README, sitemap.xml, navigation, content-briefs, internal-linking, schema-and-entities,
STRATEGY + 9 per-page specs + manifest).

**Reuses:** `recommender/draft.py` (`draft_missing_page`/`PageDraft`), `reference/blueprint.py`,
`processor/coverage_diff.py`. **Deferred (thin follow-ups):** ZIP/PDF export of the bundle
(reuse `report/pdf.py`).

**Acceptance — met:** for any blueprint/run, produce a bundle whose pages a developer can
build without further interpretation; sitemap.xml parses; JSON-LD is code-built; deterministic
scaffold with the LLM off.

---

## SP-4a · HTTP API (FastAPI) — ✅ DONE

**Goal:** the integration seam the guided UI calls — a thin FastAPI layer over the `aeo`
package (no business logic in the API).

**Delivered**
- `src/aeo/api/app.py` — endpoints from `PRODUCT_FLOW.md` §3, each delegating to an existing
  function: `GET /api/health`, `POST /api/plan` (brief → blueprint + no_website strategy,
  SP-2), `POST /api/blueprint`, `POST /api/deliverables` (inline asset bundle, SP-3),
  `POST /api/profile` (live site, reuses `Orchestrator.dry_run`), `GET /api/site-report/{run}`.
- CLI `aeo serve --host --port [--reload]`; `[api]` optional extra (fastapi, uvicorn) in pyproject.

**Verification:** 515 unit tests pass (+6 `test_api.py` via `TestClient`, `importorskip`-guarded),
ruff clean, mypy clean on the API module. Health never 500s without a DB; `/plan` & `/deliverables`
are deterministic with `use_llm=false` (no DB/network).

**Reuses:** `intelligence/brief.py`, `reference/generator.py`, `report/packager.py`,
`pipeline/orchestrator.py` (dry_run). **Dependencies:** SP-1, SP-2, SP-3.

## SP-4b · Guided UI (Next.js) — ✅ DONE (v1)

**Goal:** the consultant wizard (see [`PRODUCT_FLOW.md`](../product/PRODUCT_FLOW.md)) over the SP-4a API.

**Delivered** — `web/` workspace: Next.js 15 (App Router) + TypeScript + Tailwind.
- `lib/types.ts` + `lib/api.ts` — typed client mirroring the SP-4a contract (no business logic).
- `app/page.tsx` — the wizard (client component): a brief form with two modes —
  **Plan a new site** (`POST /api/plan` → ScenarioHeader + Strategy/Action-plan + Ideal-Sitemap
  tabs + **Deliverables** tab that calls `POST /api/deliverables` and downloads each asset) and
  **Analyze an existing site** (`POST /api/profile` → strategy/action plan). Errors and
  loading states handled; reads as a consultant (leads with scenario/headline/narrative).
- Configs (Next/TS/Tailwind/PostCSS), `.env.example` (`NEXT_PUBLIC_API_BASE`), README with run steps.

**Verification:** `npm install` + `npm run build` succeed — **compiled + type-checked clean** on
Next 15.5.19 (TS strict). UX itself needs a browser (not verifiable in CI here); the typed
contract + production build are the gate.

**Reuses:** the entire SP-1–4a stack via REST. **Run:** `aeo serve` (backend) + `npm run dev` (web).

**v2 delivered:** the full **9-step wizard** (Business Info → Goals → Website Info → Competitors →
Challenges → Analysis → Blueprint → Implementation Plan → Deliverables) with a stepper, per-step nav,
and an analysis gate; **bundle-as-zip** download — backend `AssetBundle.to_zip_bytes()` +
`POST /api/deliverables.zip` (517 tests, +2) and a "Download all (.zip)" button. Both verified
(pytest + `npm run build` clean).

**v3 delivered:** the async **deep-audit** flow. Backend — `api/jobs.py` (in-process `JobRegistry` +
`execute_audit` with an **injectable runner**; `default_audit_runner` registers the target and runs the
v4 `audit_cycle`) + `POST /api/audit` (BackgroundTask) / `GET /api/audit/{job_id}` polling. Frontend —
a "Deep audit" option (Website-Info radios: plan / quick / deep) that starts the job, polls status, shows
an `AuditProgress` panel, and loads the resulting site report's strategy. Verified: 523 tests (+6, runner
faked — no DB/crawl) + `npm run build` clean.

**v4 delivered:** **API-key auth**. `settings.ApiCfg.auth_key` (`AEO__API__AUTH_KEY`); a global
`require_api_key` dependency gates every `/api/*` route except `/api/health` (and non-`/api/` paths like
`/docs`) when a key is set, else open for dev. Frontend sends `X-API-Key` from `NEXT_PUBLIC_API_KEY`.
Verified: 525 tests (+2: open-mode + enforced 401/200, health stays open) + `npm run build` clean.

**Deferred (v5):** move the deep audit off `BackgroundTasks` onto the DB-backed `pipeline/worker.py` queue
for multi-worker scale, and richer sign-in (OAuth) if this becomes customer-facing.

---

## Build-order rationale

1. **SP-1 first** — the brain unblocks everything; without classification/routing the
   deliverables and UI have nothing to present. (Done.)
2. **SP-2 + SP-3 next, in parallel** — they produce the artifacts (blueprint-from-brief,
   asset bundle) the UI shows; independent of each other, both depend only on SP-1.
3. **SP-4 last** — a thin presentation layer over real, tested capabilities, so the UI is
   never blocked on backend logic and stays a shell.

## Risks & how the contract is preserved

| Risk | Mitigation |
|---|---|
| Scope creep into a rebuild | Every SP is additive over the validated v4 engine; no rubric/blueprint/scorer changes. |
| LLM dependence creeping into the spine | Deterministic-first enforced per SP; LLM stays an optional enrichment (tiebreak / prose), with fallbacks + a determinism test. |
| Persistence churn | SP-1 used JSONB (no migration). A dedicated `site_profiles` table is optional and additive if trending is wanted. |
| API layer duplicating logic | SP-4's FastAPI endpoints call existing `aeo` functions only — no reimplementation. |
| UI built on shifting artifacts | SP-4 gated on SP-2/SP-3 so Steps 7–9 have real deliverables; API contract designed now as the stable seam. |

## Status summary

| Sub-project | State |
|---|---|
| SP-1 Intelligence Layer | ✅ shipped + verified + merged to `main` |
| SP-2 No-Website Entry | ✅ shipped + merged to `main`; `aeo plan` |
| SP-3 Asset Packager | ✅ shipped + merged to `main`; `aeo deliverables` / `aeo plan --bundle` |
| SP-4a HTTP API (FastAPI) | ✅ shipped + merged to `main`; `aeo serve` |
| SP-4b Guided UI (Next.js) | ✅ shipped v1 (`feature/sp4b-web`); `web/` — build clean |
