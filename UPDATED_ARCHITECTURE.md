# Updated Architecture — AEO Pipeline, Outcome-Driven Reframe

**Status:** SP-1 (Backend Intelligence Layer) shipped. SP-2/3/4 designed, not yet built.
**Audience:** project leadership + the AEO engineering team.
**Companion docs:** [`USER_SCENARIOS.md`](USER_SCENARIOS.md), [`PRODUCT_FLOW.md`](PRODUCT_FLOW.md), [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), and the design spec at `docs/superpowers/specs/2026-06-09-aeo-intelligence-layer-design.md`.

---

## 1. The shift in one sentence

The pipeline used to ask *"how does this page score?"* — it now also asks
**"who is this user, how big and organized is their site, what journey stages are
missing, and therefore what should we DO for them?"** and answers with a prioritized,
scenario-specific plan instead of a bare score.

This was achieved by **reframing and extending** the validated v4 engine — not
rebuilding it. The deterministic-first crawl → extract → score → blueprint → coverage
machinery is intact and still passes its full test suite; we inserted a thin
**intelligence layer** on top and re-pointed the deliverables at it.

| | Before (auditor) | After (consultant) |
|---|---|---|
| Mental model | "Is this page up to code?" | "What is this user trying to achieve, and how do we help immediately?" |
| Single-page site | Scores ~0%, no guidance | Classified `single_page` → Restructuring Roadmap + expansion plan |
| Output | A report / scorecard | Scenario + headline + narrative + **prioritized action plan** |
| Unit of reasoning | Page + rubric criterion | Site (tier + structure) × business model × journey × coverage |

---

## 2. System map

```mermaid
flowchart TD
    subgraph Inputs
      DOM[Domain or business brief]
      DC[config/domains/*.yaml<br/>onboarding: topic, engine_target]
    end

    subgraph Existing v4 engine "(reused, unchanged)"
      DISC[crawl/discovery.py<br/>Site Discovery] --> PRI[crawl/prioritize.py<br/>classify + rank → ScoredUrl]
      PRI --> BP[reference/generator.py<br/>versioned Blueprint]
      BP --> COV[processor/coverage_diff.py<br/>CoverageDiffResult]
    end

    subgraph "SP-1 Intelligence Layer (NEW)"
      CLS[classification.py<br/>SiteClass + StructureProfile]
      BIZ[business_intent.py<br/>BusinessModel]
      JRN[journey.py<br/>5-stage JourneyCoverage]
      SCN[scenario.py<br/>Scenario Router → StrategyPlan]
      SP[site_profile.py<br/>SiteProfile.to_dict]
      CLS --> SCN
      BIZ --> SCN
      JRN --> SCN
      CLS & BIZ & JRN & SCN --> SP
    end

    DOM --> DISC
    DC --> BP
    PRI --> CLS
    PRI --> BIZ
    PRI --> JRN
    COV --> JRN
    COV --> SCN

    SP --> OUT1[aeo profile DOMAIN<br/>read-only CLI]
    SP --> OUT2[dry_run preview<br/>zero-DB]
    SP --> OUT3[site_reports.sections.strategy<br/>persisted JSONB]
    OUT3 --> RPT[report/site_builder.py<br/>STRATEGY block]
    SP -. SP-4 .-> API[FastAPI + React/Next UI]
```

The intelligence layer is **pure**: callers hand it the already-discovered, classified
inventory (`list[ScoredUrl]`) and an optional in-memory `CoverageDiffResult`. It never
does I/O of its own. The only non-deterministic seam is an *optional* LLM tiebreak in
business-model detection (see §5).

---

## 3. The intelligence package (`src/aeo/intelligence/`)

| Module | Responsibility | Output type |
|---|---|---|
| `signals.py` | `PageView` view + `token_hit` matcher (word / phrase / `stem*` prefix; `_`≡`-`) + `to_page_views` | — |
| `config.py` | `IntelligenceCfg` + `load_intelligence_cfg()` over `config/intelligence.yaml` (code defaults) | `IntelligenceCfg` |
| `classification.py` | **Website Classification** — tier from page count + structural quality (essential archetypes present/missing) | `SiteClass`, `Classification` |
| `business_intent.py` | **Business-Model engine** — weighted slug signals + industry hints; optional LLM tiebreak | `BusinessModel`, `BusinessIntent` |
| `journey.py` | **Journey Coverage** — 5-stage funnel mapping + gap → filling-node hints | `Stage`, `JourneyCoverage` |
| `scenario.py` | **Scenario Router** — the brain: routes to a scenario + assembles a prioritized `StrategyPlan` | `Scenario`, `StrategyPlan` |
| `site_profile.py` | Aggregate + JSONB serialization | `SiteProfile` |

### The `SiteProfile` contract

`SiteProfile.to_dict()` is the single JSONB-serializable object every consumer (site
report, CLI, dry-run, future API/UI) depends on. Shape:

```jsonc
{
  "domain": "acme.com",
  "scenario": "small_site",
  "deliverable": "Gap Analysis & Build Plan",
  "headline": "…",            // one-line consultant framing
  "narrative": "…",           // what we'll do & why (templated; LLM-upgradable later)
  "agency_mode": false,
  "classification": { "site_class": "small", "page_count": 6, "structure_score": 0.5,
                      "type_distribution": {…}, "present_archetypes": [...], "missing_archetypes": [...] },
  "business_intent": { "model": "saas", "confidence": 0.42, "decided_by": "deterministic",
                       "evidence": [...], "scores": {…} },
  "journey": { "stages": [{ "stage": "awareness", "present_count": 2, "covered": true, "examples": [...] }, …],
               "gaps": ["consideration", "conversion"], "filling_nodes": { "conversion": ["/contact"] } },
  "actions": [ { "priority": 1, "title": "…", "detail": "…", "category": "structure",
                 "effort": "low", "related_slugs": [...] }, … ]
}
```

---

## 4. Wiring — four seams, no new table

The profile is surfaced at exactly four points; none changes an existing signature in a
breaking way (new params are keyword-only with defaults).

1. **`dry_run` (zero-DB preview)** — `orchestrator.dry_run()` builds the profile from the
   in-memory `scored` + `cov` and returns it under `output["profile"]`. Best-effort
   isolated (a profile error degrades to `None`, never aborts the preview).
2. **Persistence (no migration)** — `reference_arch.compute_and_persist_coverage()` builds
   the profile and embeds it in the existing `coverage_diffs.detail` JSONB under
   `"site_profile"`. `CoverageDiffResult.from_detail` reads only its own keys, so the
   extra key round-trips harmlessly. The build is wrapped best-effort.
3. **Site report** — `orchestrator._build_and_persist_site_report()` lifts
   `detail["site_profile"]` into `build_site_report(site_profile=…)`, which adds
   `sections["strategy"]`; `render_site_report` prints a `STRATEGY` block + a one-line
   scenario summary.
4. **CLI** — new read-only `aeo profile DOMAIN` (discover → prioritize → deterministic
   in-memory blueprint + coverage → `build_site_profile` → print). `--llm/--no-llm`,
   `--json`. Writes nothing to the DB (like `aeo discover`).

> **Why JSONB, not a new table?** SP-1's goal was to add the brain with zero migration
> risk. `coverage_diffs.detail` already exists and is run-scoped. A dedicated
> `site_profiles` table (independently queryable) is a clean SP-2/SP-3 follow-up once we
> want to trend profiles over time.

---

## 5. Deterministic-first — where the LLM and DB sit

"Deterministic-first" is the project's pre-existing contract: **every stage produces a
correct, complete answer with the LLM off; the LLM is an optional enrichment that never
gates.** SP-1 honors it strictly (verified by an adversarial review of the contract):

- **Classification, journey, scenario routing, action assembly** — 100% deterministic.
- **Business-model detection** — deterministic weighted signals decide the winner. The
  LLM is consulted *only* when the top two models are within `llm_tiebreak_margin` of
  each other, and *only* to choose among the already-tied labels. On no LLM, LLM error,
  or an invalid choice, it falls back to the deterministic winner. With no signal at all,
  the conservative `LEAD_GEN` default is returned. The result records `decided_by`.
- **Persistence** — PostgreSQL is the spine, unchanged. The full `audit-cycle` path writes
  everything to Postgres as before; only `dry_run` and `aeo profile` are deliberately
  zero-DB.
- **Generative deliverables downstream** (blueprint synthesis, content drafts) remain
  LLM-powered when keyed, with deterministic fallbacks — unchanged by SP-1.

The reason the *brain* is deterministic isn't to avoid the LLM — it's that "what tier is
this site / what's missing / which scenario" should be **computed, not guessed**.

---

## 6. Configuration

`config/intelligence.yaml` layers over code defaults via `load_intelligence_cfg()`
(`lru_cache`d), exactly like `prioritization.yaml`. Tunables: tier thresholds, archetype
definitions, per-model signal weights, `llm_tiebreak_margin`, industry hints, 5-stage
journey signals, scenario map + deliverable labels. Delete the file and the engines still
run on built-in defaults. Invalid/typo'd model keys in the YAML are ignored, never crash.

---

## 7. What is reused vs new

| Concern | Status | Module |
|---|---|---|
| Site discovery, prioritization, page-type classify | **reused** | `crawl/discovery.py`, `crawl/prioritize.py` |
| Versioned ideal-site blueprint | **reused** | `reference/generator.py`, `reference/framework*.py` |
| Site-level coverage diff (missing/thin) | **reused** | `processor/coverage_diff.py` |
| Content drafts / page briefs | **reused** | `recommender/draft.py` |
| Per-page + site reports, PDF | **reused** | `report/*` |
| **Site classification (tier + structure)** | **new** | `intelligence/classification.py` |
| **Business-model engine** | **new** | `intelligence/business_intent.py` |
| **5-stage journey coverage** | **new** | `intelligence/journey.py` |
| **Scenario router + strategy plan** | **new** | `intelligence/scenario.py` |
| **SiteProfile aggregate + wiring** | **new** | `intelligence/site_profile.py` + 4 seams |

---

## 8. How SP-2/3/4 extend this

- **SP-2 (No-Website entry path)** feeds `build_site_profile` with synthetic/empty
  discovery + a business brief; the router already handles `SiteClass.NONE → no_website`.
- **SP-3 (Asset Packager)** consumes the `SiteProfile` + blueprint + `recommender/draft.py`
  to emit a developer-ready bundle (sitemap.xml, nav spec, per-page specs, content briefs).
- **SP-4 (UI)** is a FastAPI service exposing `SiteProfile`/blueprint/deliverables over
  REST, with a React/Next guided wizard — see [`PRODUCT_FLOW.md`](PRODUCT_FLOW.md).

Each is additive and preserves the deterministic-first contract.
