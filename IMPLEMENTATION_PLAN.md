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

## SP-2 · No-Website / Business-Input Entry Path — NEXT

**Goal:** serve Scenario 1 (no website) end-to-end — turn a business brief into a packaged
AEO Website Blueprint with **no crawl**.

**Scope / files**
- `intelligence` or `reference`: a `BusinessInput` model (name, industry, location,
  services[], competitors[], goals[]).
- New orchestrator path `Orchestrator.blueprint_from_brief()` (or extend `dry_run`):
  brief → `framework bootstrap --category` → `generate_blueprint` (crawl-free) →
  `build_site_profile(discovered=[], …)` → packaged output.
- CLI: `aeo blueprint-from-brief` (or extend `aeo onboard` with a `--no-site` mode).
- Plumb `category` from the brief into `build_site_profile` (the `industry_hints` seam,
  noted by review as currently unreached by live wiring).

**Reuses:** `reference/framework_bootstrap.py`, `reference/generator.py`,
`reference/onboard.py`, `reference/competitor_discovery.py`, the SP-1 router (already
handles `SiteClass.NONE`).

**Dependencies:** SP-1. **Effort:** ~S–M (mostly an entry point + input model; the engine
exists).

**Acceptance:** a brief with no domain produces a full blueprint + `no_website` strategy +
verified competitors, writing nothing that requires a live site; `category` measurably
shifts the business model and entity ceiling.

---

## SP-3 · Implementation Asset Packager — NEXT

**Goal:** turn the blueprint + strategy into a **developer-ready bundle** the user can
implement directly.

**Scope / files**
- `report/packager.py`: assemble a bundle from a `SiteProfile` + `Blueprint` + drafts:
  - `sitemap.xml` (the ideal sitemap),
  - navigation hierarchy spec (clusters → pillar → supporting),
  - per-page spec sheets (H1, sections, FAQ, JSON-LD — from `recommender/draft.py`),
  - content briefs (priority-ordered missing pages),
  - internal-linking plan, schema/entity recommendations.
- Export formats: Markdown bundle, ZIP, PDF (reuse `report/pdf.py`), and JSON.
- CLI: `aeo deliverables -r RUN_ID` / `--from-blueprint`.

**Reuses:** `recommender/draft.py` (`PageDraft`, `draft_site_pages`), `report/*`,
`report/pdf.py`.

**Dependencies:** SP-1 (strategy/actions); pairs with SP-2 (blueprint source).
**Effort:** ~M (assembly + formatters; the content generation exists).

**Acceptance:** for any run/blueprint, produce a downloadable bundle whose pages a
developer can build without further interpretation; JSON-LD validates; deterministic
scaffold when the LLM is off.

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
| SP-1 Intelligence Layer | ✅ shipped + verified + pushed (`feature/sp1-intelligence-layer`) |
| SP-2 No-Website Entry | 📋 specced here, not started |
| SP-3 Asset Packager | 📋 specced here, not started |
| SP-4 Guided UI (FastAPI + React/Next) | 📋 designed in `PRODUCT_FLOW.md`, not started |
