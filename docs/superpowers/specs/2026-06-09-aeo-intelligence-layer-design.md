# AEO Intelligence Layer (SP-1) — Design Spec

**Date:** 2026-06-09
**Author:** Kenneth (Securin AEO) + Claude
**Status:** Approved (design), pre-implementation
**Branch:** `feature/sp1-intelligence-layer`

---

## 1. Context & problem

The current AEO pipeline (the validated v4 "system of record", System A) is **page-and-rubric
centric**: it discovers a site, prioritizes pages, crawls/extracts/scores them against a
10-criterion rubric, and produces per-page + site-level reports. It already has a strong
*topic* layer — the versioned Blueprint Generator, Coverage Diff, framework bootstrap, content
drafting, and onboarding.

A product-review meeting surfaced a different complaint: the system **thinks like an auditor, not
a consultant.** Concretely:

- A **single-page website just scores badly** — the tool offers no plan to fix it.
- The pipeline has **no model of who the user is** (business model), **how organized their site
  is** (structure, not just page count), or **which journey stages they're missing.**
- Output is *a report*, not *"here is what to do, in order."*

The decision (locked with the user): **reframe + extend, not rebuild.** Keep the deterministic-first,
tested v4 engine; insert a thin **intelligence layer** that turns raw discovery + coverage into a
**consultant's answer**: *who is this user, how big/organized is their site, what's missing, and
therefore what should we DO for them?*

This spec covers **SP-1 only** — the backend intelligence layer. SP-2 (no-website crawl-free entry
path), SP-3 (developer-ready asset packager), and SP-4 (FastAPI + React/Next guided UI) are separate
sub-projects, designed in the product docs and built later.

---

## 2. Goals / non-goals

**Goals**
- A new `src/aeo/intelligence/` package with four deterministic-first engines + an aggregate.
- Wire the aggregate (`SiteProfile`) into the existing pipeline at minimal seams: the zero-DB
  `dry_run` preview, the persisted site report, the site-report renderer, and a new read-only
  `aeo profile DOMAIN` command.
- Config-over-code (`config/intelligence.yaml`), mirroring `prioritization.yaml`.
- Full unit-test coverage; deterministic output with the LLM off; **no DB migration.**

**Non-goals (this turn)**
- The no-website business-input entry path (SP-2).
- Packaged dev-ready deliverable bundles — sitemap.xml, per-page spec sheets (SP-3).
- The web UI (SP-4).
- Any change to the blueprint contract, the rubric, or existing scorers.

---

## 3. Architecture

### 3.1 Where it sits

```
discover ─► prioritize ─► [coverage diff] ─┐
(ScoredUrl[])              (CoverageDiff)   ├─► intelligence.build_site_profile() ─► SiteProfile
domain_config (topic/      (in run_site /   │        │
 category/engine_target) ──┘ dry_run)        │        ├─► dry_run output["profile"]   (zero-DB)
                                             │        ├─► site_reports.sections["strategy"] (persisted JSONB)
                                             │        └─► aeo profile DOMAIN           (read-only CLI)
```

The layer is **pure** (no I/O of its own): callers hand it the already-discovered, already-classified
inventory (`list[ScoredUrl]` from the prioritizer) and an optional in-memory `CoverageDiffResult`.
The optional `LLMClient` is used only as a *tiebreak* in business-model detection; everything else is
deterministic. This mirrors `processor/coverage_diff.py` (pure, caller supplies inputs).

### 3.2 New package layout

```
src/aeo/intelligence/
  __init__.py            # public exports: build_site_profile, SiteProfile, SiteClass, BusinessModel, Scenario, …
  config.py              # IntelligenceCfg dataclass + load_intelligence_cfg() (YAML, lru_cache) — mirrors prioritize
  classification.py      # Website Classification Engine  -> Classification (SiteClass + StructureProfile)
  business_intent.py     # Business-Model Engine          -> BusinessIntent
  journey.py             # Journey Coverage Engine         -> JourneyCoverage  (5-stage)
  scenario.py            # Scenario Router (the brain)      -> StrategyPlan
  site_profile.py        # aggregate build_site_profile()   -> SiteProfile
config/intelligence.yaml # thresholds, signal weights, stage maps, scenario map
```

---

## 4. Data model

All dataclasses are `@dataclass(slots=True)` and JSONB-serializable (str-enum values, plain
containers). Enums subclass `str, Enum` so `to_dict()` is JSON-native.

### 4.1 Classification (`classification.py`)

```python
class SiteClass(str, Enum):
    NONE = "none"; SINGLE_PAGE = "single_page"; SMALL = "small"
    MEDIUM = "medium"; LARGE = "large"; ENTERPRISE = "enterprise"

# Essential page archetypes a *discoverable* site should have (structure > count).
# Detected from BOTH the prioritizer's page_type AND slug tokens (config-driven).
@dataclass(slots=True)
class StructureProfile:
    page_count: int
    type_distribution: dict[str, int]      # page_type -> count
    present_archetypes: list[str]
    missing_archetypes: list[str]
    structure_score: float                 # 0..1 = present / expected-for-model

@dataclass(slots=True)
class Classification:
    site_class: SiteClass
    structure: StructureProfile
    signals: dict[str, Any]                # raw counts for explainability
```

**Tier thresholds** (config defaults): `NONE`=0, `SINGLE_PAGE`=1, `SMALL`=2–10, `MEDIUM`=11–50,
`LARGE`=51–200, `ENTERPRISE`=>200. Driven by `discovered` length.

**Archetype detection** is the load-bearing "structure > count" idea: a page is an *about / contact /
services / industries / faq / resources / case_studies / blog / product* archetype when its
`page_type` is in the archetype's `page_types` **or** its slug contains one of the archetype's
`slug_tokens` (config). `structure_score` = present archetypes ÷ archetypes expected for the detected
business model (so a SaaS site isn't penalized for lacking "industries", but a single-pager is
flagged for lacking everything).

### 4.2 Business intent (`business_intent.py`)

```python
class BusinessModel(str, Enum):
    LEAD_GEN = "lead_gen"; ECOMMERCE = "ecommerce"; LOCAL = "local"
    SAAS = "saas"; AGENCY = "agency"; PUBLISHER = "publisher"; ENTERPRISE = "enterprise"

@dataclass(slots=True)
class BusinessIntent:
    model: BusinessModel
    confidence: float                      # top_score / sum_scores  (0..1)
    evidence: list[str]                    # human-readable signals that fired
    scores: dict[str, float]               # model -> raw score (explainability)
    decided_by: str                        # "deterministic" | "llm-tiebreak" | "default"
```

**Deterministic scoring:** each model has weighted slug-token signals (e.g. ecommerce: `/cart`+3,
`/checkout`+3, `/shop`+2; saas: `/pricing`+3, `/demo`+2; local: `/locations`+3, `/appointment`+3;
agency: `/case-studies`+3, `/portfolio`+3; publisher: a `blog_ratio` weight when blog pages exceed a
threshold; enterprise: `/investors`+3, large site-class bonus). Scores are summed across discovered
slugs. The onboarding `category`/`topic` (from `domain_config`) nudges via `industry_hints`.

**LLM tiebreak (optional enrichment):** when the top two model scores are within
`llm_tiebreak_margin` and an enabled `LLMClient` is supplied, ask it (industry + a sample of slugs)
to pick one of the closed model labels; on any failure or no-LLM, fall back to the deterministic top
score. `decided_by` records which path won. **With the LLM off, a complete, correct answer is always
produced** (default `LEAD_GEN` when no signal fires — the conservative B2B default).

### 4.3 Journey coverage (`journey.py`)

```python
class Stage(str, Enum):                    # the EXTENDED 5-stage model
    AWARENESS = "awareness"; CONSIDERATION = "consideration"; DECISION = "decision"
    CONVERSION = "conversion"; RETENTION = "retention"

@dataclass(slots=True)
class StageCoverage:
    stage: Stage
    present_count: int
    examples: list[str]                    # up to N example slugs
    covered: bool

@dataclass(slots=True)
class JourneyCoverage:
    stages: list[StageCoverage]
    gaps: list[Stage]                      # uncovered stages, in funnel order
    filling_nodes: dict[str, list[str]]    # stage value -> missing blueprint slugs that fill it
```

> **Note on the two journey vocabularies.** The blueprint contract
> (`reference/blueprint.py`) has a *closed 3-value* `JourneyStage` Literal
> (`awareness/consideration/decision`) — **unchanged, it is the contract.** The
> intelligence layer defines its *own* richer 5-stage `Stage` enum (adds
> `conversion`, `retention`) for the funnel-gap view the meeting asked for. The two
> are deliberately separate; the journey engine maps discovered pages + the 3 blueprint
> stages onto the 5-stage model via config (`journey.stage_signals`), so we get the
> richer funnel view without touching the contract.

**Mapping:** each discovered page is assigned to a stage by `page_type` + slug tokens (config):
awareness←blog/pillar/"what-is"; consideration←solution/"vs"/compare; decision←product/pricing/demo;
conversion←contact/signup/checkout/quote/book; retention←support/docs/help/faq/account. A stage is
`covered` when ≥1 page maps to it. `gaps` = uncovered stages. `filling_nodes` maps each gap to the
missing blueprint nodes (from the coverage diff) whose slug pattern serves that stage — so a gap
comes with concrete "build these" suggestions.

### 4.4 Scenario router (`scenario.py`) — the brain

```python
class Scenario(str, Enum):
    NO_WEBSITE = "no_website"; SINGLE_PAGE = "single_page"; SMALL_SITE = "small_site"
    GROWING_SITE = "growing_site"; MATURE_SITE = "mature_site"

@dataclass(slots=True)
class StrategyAction:
    title: str; detail: str
    priority: int                          # 1 = highest
    effort: str                            # "low" | "medium" | "high"
    category: str                          # structure | content | journey | authority | entity | schema | linking
    related_slugs: list[str]

@dataclass(slots=True)
class StrategyPlan:
    scenario: Scenario
    headline: str                          # the consultant's one-line framing
    narrative: str                         # what we'll do & why (deterministic template; LLM-upgradable later)
    deliverable: str                       # e.g. "AEO Website Blueprint", "Restructuring Roadmap",
                                           #      "Gap Analysis & Build Plan", "Consolidation & Authority Plan"
    actions: list[StrategyAction]          # prioritized
    agency_mode: bool                      # client-ready packaging (BusinessModel.AGENCY or flag)
```

**Routing** is a deterministic matrix:

| `SiteClass` | `Scenario` | Primary deliverable | Dominant action categories |
|---|---|---|---|
| NONE | NO_WEBSITE | AEO Website Blueprint | structure (full sitemap) |
| SINGLE_PAGE | SINGLE_PAGE | Restructuring Roadmap | structure → content |
| SMALL | SMALL_SITE | Gap Analysis & Build Plan | content, journey, entity |
| MEDIUM | GROWING_SITE | Gap Analysis & Authority Plan | content, authority (thin clusters), journey |
| LARGE / ENTERPRISE | MATURE_SITE | Consolidation & Authority Plan | authority, linking, consolidation, schema |

**Action assembly** draws from every engine, then a deterministic prioritizer orders them:
- **structure** actions ← missing archetypes (from `Classification`), weighted hardest for tiny sites.
- **content** actions ← missing blueprint nodes (from `CoverageDiffResult.missing_by_priority`).
- **journey** actions ← `JourneyCoverage.gaps` (+ their `filling_nodes`).
- **authority** actions ← `thin_clusters`.
- **entity/schema** actions ← low coverage % / missing required entities.

`agency_mode` (set when `BusinessModel.AGENCY` or an explicit flag) adds an executive-summary framing
to `narrative` and marks the deliverable client-ready — satisfying Scenario 5 without a separate code
path.

`narrative`/`headline` are **deterministic templates** by default; an optional LLM pass can rewrite
them into prose later (SP-2+), behind the same enrichment seam — out of scope here.

### 4.5 Aggregate (`site_profile.py`)

```python
@dataclass(slots=True)
class SiteProfile:
    domain: str
    classification: Classification
    business_intent: BusinessIntent
    journey: JourneyCoverage
    strategy: StrategyPlan
    def to_dict(self) -> dict[str, Any]: ...   # JSONB payload for site_reports.sections["strategy"]
    def headline(self) -> str: ...             # one-line summary for logs / CLI

def build_site_profile(
    *,
    domain: str,
    discovered: list[ScoredUrl],
    coverage: CoverageDiffResult | None = None,
    topic: str | None = None,
    category: str | None = None,
    llm: LLMClient | None = None,
    cfg: IntelligenceCfg | None = None,
) -> SiteProfile: ...
```

`build_site_profile` runs classification → business_intent → journey → scenario in order and assembles
the aggregate. Pure except for the optional LLM tiebreak; `cfg` defaults to `load_intelligence_cfg()`.

---

## 5. Configuration (`config/intelligence.yaml`)

Loaded by `load_intelligence_cfg()` (lru_cached, via `settings.load_yaml_file`), exactly like
`load_prioritization_cfg()`. Sections: `classification.thresholds`, `classification.archetypes`,
`business_model.signals` + `llm_tiebreak_margin` + `industry_hints`, `journey.stage_signals`,
`scenario.map` + `scenario.deliverables`. Sensible code defaults so the engines work even if the
YAML is absent (mirrors how `prioritize` has `DEFAULT_*`).

---

## 6. Wiring (4 seams)

1. **`pipeline/orchestrator.py` · `dry_run`** — build the profile from the in-memory `scored` +
   `cov` and add it to the output under `"profile"`. Zero-DB; this is the demo/onboarding preview, so
   the classification + scenario + action plan show up with no persistence.
2. **`pipeline/orchestrator.py` · `run_site` → site report** — compute the in-memory coverage diff
   once, build the profile, and persist it so the site report carries it. To avoid a migration the
   profile rides in the existing `coverage_diffs.detail` JSONB under `"site_profile"`; the site-report
   builder lifts it into `sections["strategy"]`. *(A dedicated `site_profiles` table is a clean
   SP-2/3 follow-up if we want it independently queryable.)*
3. **`report/site_builder.py` + `render_site_report`** — add a `"strategy"` section to the assembled
   report and a `STRATEGY` block to the text rendering (scenario, tier, business model, journey gaps,
   top prioritized actions).
4. **`cli.py` · new `aeo profile DOMAIN`** — read-only, like `aeo discover`: discover → prioritize →
   (deterministic in-memory blueprint + coverage, as `dry_run` does) → `build_site_profile` → pretty
   print. `--llm/--no-llm` toggles the business-model tiebreak; no DB writes.

No existing function signature changes in a breaking way; new params are keyword-only with defaults.

---

## 7. Testing

Unit tests (deterministic, no network, no DB), one module each:

- `test_classification.py` — tier thresholds at every boundary (0,1,2,10,11,50,51,200,201);
  archetype detection from page_type and from slug tokens; `structure_score` math; empty input.
- `test_business_intent.py` — each `BusinessModel` from a representative slug fixture; confidence;
  `decided_by` deterministic path; LLM-tiebreak path with a **fake LLM**; industry-hint boost;
  no-signal → `LEAD_GEN` default.
- `test_journey.py` — 5-stage mapping; gap detection; `filling_nodes` from a fake coverage diff;
  fully-covered site → no gaps.
- `test_scenario.py` — the full routing matrix (each `SiteClass` → `Scenario` + deliverable); action
  assembly from each source; deterministic priority ordering; `agency_mode` overlay.
- `test_site_profile.py` — `build_site_profile` end-to-end with fakes; `to_dict()` JSONB round-trip;
  identical output across two runs (determinism) with the LLM off.
- `test_intelligence_wiring.py` — profile present in `dry_run` output; `sections["strategy"]` in a
  built site report; `aeo profile` via Typer's `CliRunner` (or the underlying function) on a fake
  discovery.

Target: parity with the project bar — **ruff clean, mypy clean on new modules, all new tests green**,
and the existing suite (424 passed / 1 skipped) unaffected.

---

## 8. Risks & mitigations

- **Two journey vocabularies confusion** → documented explicitly (§4.3); the 5-stage enum lives only
  in `intelligence/`, the contract's 3-value Literal is untouched.
- **Profile-in-coverage-detail is a slight semantic stretch** → accepted, migration-free, run-scoped;
  flagged as a candidate for its own table in SP-2/3.
- **Business-model misclassification on sparse sites** → confidence + evidence are surfaced, default is
  the conservative `LEAD_GEN`, and the optional LLM tiebreak only engages on genuine ties.
- **Determinism** → enforced by a test; the LLM never gates, only breaks ties.

---

## 9. Deliverables this turn

1. The `src/aeo/intelligence/` package (6 modules) + `config/intelligence.yaml`.
2. The 4 wiring seams.
3. The 6 test modules.
4. The four product docs the meeting required: `UPDATED_ARCHITECTURE.md`, `PRODUCT_FLOW.md`
   (incl. the FastAPI + React/Next SP-4 design + API contract), `USER_SCENARIOS.md`,
   `IMPLEMENTATION_PLAN.md` (the SP-1→SP-4 roadmap).
5. Verification: tests + ruff + mypy, and an adversarial review pass.
