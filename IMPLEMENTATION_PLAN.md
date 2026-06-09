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

## SP-4 · Guided UI Application — LATER

**Goal:** the 9-step consultant wizard (see [`PRODUCT_FLOW.md`](PRODUCT_FLOW.md)).

**Scope / files**
- `api/` — a **FastAPI** service wrapping the `aeo` package (endpoints in
  `PRODUCT_FLOW.md` §3); long crawls dispatched to the existing `pipeline/worker.py`
  queue with a job-status endpoint.
- `web/` — a **React/Next** frontend: the 9-step wizard rendering `SiteProfile` /
  blueprint / plan JSON.
- Packaging: `pip install -e ".[api]"` extra (fastapi, uvicorn); the frontend is a
  separate `web/` workspace.

**Reuses:** every SP-1–3 capability via function calls; no business logic in the API layer.

**Dependencies:** SP-1, SP-2, SP-3. **Effort:** ~L (new API + frontend stack; its own
brainstorm + visual mockups before build).

**Acceptance:** a user completes Steps 1–9 and downloads a blueprint + implementation plan;
"no website" and "single page" inputs never dead-end; the experience reads as a consultant,
not an audit tool.

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
| SP-3 Asset Packager | ✅ shipped + verified (`feature/sp3-asset-packager`); `aeo deliverables` / `aeo plan --bundle` |
| SP-4 Guided UI (FastAPI + React/Next) | 📋 designed in `PRODUCT_FLOW.md`, not started |
