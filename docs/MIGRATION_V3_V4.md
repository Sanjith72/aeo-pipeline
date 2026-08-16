# V3 → V4 Migration Report

How the AEO crawler moved from the v3 *compliance checker* to the v4
*content-strategy engine*: a versioned, per-topic **Reference Architecture
Generator**, a site-level **Coverage Diff**, an **Independent Validator** that
fixes circular validation, a **parallel processor**, a **validated-wins**
feedback loop, and the **weekly audit loop** + OCI infra hooks.

This document is the senior-review-board record: what existed, what changed, why,
and what remains. Companion docs: [ARCHITECTURE.md](ARCHITECTURE.md) (how it's
built), [DEPLOYMENT.md](DEPLOYMENT.md) (how it runs), [VALIDATION.md](VALIDATION.md)
(rubric→implementation), and [aeo_architecture_v4.md](architecture/aeo_architecture_v4.md)
(the target spec).

---

## 1. Architecture assessment — V3 as found

The v3 codebase was **ahead of its own v3 design doc**. Six items the v4 spec
treats as recent or pending were already shipped, tested, and clean:

| Capability | Where (v3, already present) |
|---|---|
| 10-criterion rubric | `scoring/scorers/` (10 scorers) + migration `0008` |
| Content-hash gate (skip unchanged) | `crawl/fingerprint.py` + `pipeline/stages.copy_unchanged` |
| Page Prioritization (top-N) | `crawl/prioritize.py` |
| Observability (per-step traces) | `obs/tracing.py`, `agent_traces` |
| Error Sink (page isolation) | `obs/error_sink.py`, `page_guard` |
| Validation retry ≤3 | `validation/validator.py` |

Design strengths kept verbatim: deterministic-first scoring, config-over-code,
pure-function extractors/scorers, "one bad part never sinks the page," and
Postgres-as-the-whole-backend. The v4 work was **added around** these, not on top
of a rewrite.

Two defects were found while reading:

- **`.env` mismatch (not auto-fixed).** The repo's `.env` is a copy of the
  reference repo's env (`aeo_saas` DB, a `FIRECRAWL_API_KEY`, no `AEO__*`
  prefixes) and does not match this project's `settings.py` contract. It holds a
  live secret, so it was **left untouched**; `.env.example` was reconciled and the
  fix is documented under *Remaining Risks*.
- **"LangGraph" label vs. reality.** The v4 diagram labels the orchestrator
  "LangGraph"; the code uses a clean custom async `Orchestrator`. Decision below.

---

## 2. Migration report — component by component

Legend: **NEW** = net-new module · **EXT** = extended existing · **KEEP** =
retained unchanged with rationale.

### 2.1 Blueprint contract — `reference/blueprint.py` · **NEW** (keystone)
- **Why:** the v4 spec's "lock the blueprint JSON schema — nothing downstream is
  real until this matches." Every new block depends on it.
- **What:** Pydantic models (`Blueprint`, `SitemapNode`, `CoverageMap`,
  `CoverageCluster`) with closed vocabularies (`page_type`/`intent`/`journey_stage`
  aligned to the existing prioritizer + query-intent classifier), slug
  normalization, a content-hash of the *inputs* (for reuse-vs-bump versioning),
  and lossless JSON/JSONB round-trip. 23 tests.

### 2.2 Reference Architecture Generator — `reference/{framework,competitor_patterns,generator}.py` · **NEW**
- **L1 (empirical floor)** `competitor_patterns.py`: pure aggregation over the
  competitor pages already crawled — page-type mix, JSON-LD types, entity
  coverage, question headings, word counts.
- **L2 (guardrail + ceiling)** `framework.py` + `config/framework.yaml`: the
  curated PEV-on-Securin taxonomy (clusters → pillar + supporting nodes,
  standalone nodes, required-entity vocabulary) and the per-criterion *definitions*
  (perfect vs. average, 3–5 checkable items, schema.org mappings).
- **L3 (synthesis)** `generator.py`: deterministic blueprint always built from
  L1+L2; an LLM (Gemini via the existing cloud backend) *augments* it (extra seed
  questions, net-new supporting pages) strictly inside the guardrail — every
  proposal is re-validated against the contract; invalid/duplicate dropped; any
  failure falls back to deterministic. Versioning (reuse-or-bump) +
  run-pinning in `storage/repos/blueprints.py`. 13 tests.
- **Replaces:** the static `best_practices.yaml` as the dynamic 60% baseline
  (which is **kept** as the per-criterion target source the generator's criteria
  definitions complement).

### 2.3 Coverage Diff (site-level) — `processor/coverage_diff.py` · **NEW**
- **Why:** the new *kind* of gap — "which pages are missing," not "how good is
  this page."
- **What:** pure diff of the discovered/classified sitemap vs. the blueprint's
  ideal sitemap → missing nodes (priority-ordered → net-new content briefs) and
  thin clusters (below the authority target). Exact-slug + token-overlap matching
  with a page-type guard. `storage/repos/coverage.py`. 9 tests.

### 2.4 Independent Validator — `validation/independent.py` + `nlp/perplexity.py` · **NEW**
- **Why:** v3's validator re-scored against the **same** rubric the recommender
  optimized (circular). v4 checks signals the recommender does *not* directly
  optimize.
- **What:** three deterministic, non-circular checks (liftable ≤50-word TL;DR,
  H1-parses-as-a-question, strictly-valid JSON-LD) + an optional Perplexity
  citation test (injectable client, returns `None`/falls back when unkeyed).
  Wired into `analyze_page` as an *additional* authoritative signal feeding the
  report and the validated-wins loop. 18 + 10 tests.
- **KEEP (with rationale):** v3's `validate_page` re-score is retained as the
  *edit-efficacy* gate (does the proposed edit raise the deterministic score?) +
  its ≤3 retry. The Independent Validator adds the non-circular quality + real-world
  layer. Together they are strictly better than v3's single circular gate; default
  per-page behavior of `validate_page` is unchanged so the v3 contract holds.

### 2.5 Parallel Processor — `scoring/scorers/run_all_parallel` + analysis fan-out · **EXT**
- **Why:** v4 "the 4 criteria-agents run concurrently."
- **What:** `run_all_parallel` runs the criterion scorers in a thread pool with
  **byte-identical output** to sequential (scorers are pure over a read-only
  context; result re-ordered to the fixed registry order). Opt-in via
  `AEO__SCORING__PARALLEL`. The per-page *analysis* loop also fans out across a
  thread pool (`AEO__VALIDATION__ANALYSIS_CONCURRENCY`), each page Error-Sink
  isolated. 5 parity tests.

### 2.6 Validated-wins feedback loop — `reference/feedback.py` · **NEW**
- **Why:** controlled "the system evolves itself."
- **What:** pages that provably get cited (Perplexity) are compared, per
  criterion, against non-cited pages; where the cited cohort consistently and
  materially out-tiers the target *and* the non-cited cohort, a criterion-target
  refinement is **proposed** (`status='proposed'`, human-gated — never
  auto-applied). `storage/repos/feedback.py`, `aeo refinements`. 5 tests.

### 2.7 Weekly audit loop + infra — `Orchestrator.audit_cycle`, `crawl/transport.py`, `ops/` · **EXT/NEW**
- **What:** `aeo audit-cycle` runs discover → blueprint → coverage → crawl
  (hash-gated, unchanged pages carried forward) → analyze → site report. `ops/`
  ships a systemd service+timer, a crontab, and a runner script. `crawl/transport.py`
  forces IPv4 (`AEO__CRAWLER__FORCE_IPV4`) across discovery, PageSpeed, Perplexity,
  and the LLM backends for OCI Ampere. 3 transport tests.

### 2.8 Site-level report — `report/site_builder.py` · **NEW**
- Folds the Coverage Diff (missing/thin → content briefs) and a per-page rollup
  into one record, pinned to the blueprint version. `storage/repos/site_reports.py`,
  `aeo site-report`. 8 tests.

### 2.9 Storage — migration `0009_v4_reference_architecture.sql` · **NEW**
- Additive + idempotent: `blueprints`, `coverage_diffs`, `citation_results`,
  `criteria_refinements`, `site_reports`, and `crawl_runs.blueprint_id`. No v3
  table altered in a way that changes existing semantics.

### 2.10 Orchestrator — **KEEP async, not LangGraph** (decision)
- The v4 diagram says "LangGraph"; the v3 async `Orchestrator` is clean,
  dependency-free, and consistent with design principle #5 (Postgres is the whole
  backend). Rewriting a working orchestrator into a `StateGraph` adds a heavy
  dependency and migration risk for a cosmetic label match. **Decision:** extend
  the async orchestrator; the "LangGraph runs the graph, it doesn't schedule
  itself" note in the v4 doc is satisfied by `audit_cycle` + the systemd/cron
  schedule. Revisit only if checkpointing/resumability across nodes becomes a hard
  requirement.

---

## 3. Final V4 architecture summary

```
INPUTS: topic · client domain · competitor set
  │
  ▼  (once per run, slow cadence, versioned + pinned)
REFERENCE ARCHITECTURE GENERATOR
  L1 competitor patterns ─┐
  L2 framework + criteria ─┼─► Gemini synthesis (or deterministic) ─► Blueprint vN
                           ┘                                          (ideal sitemap + coverage map)
  │
  ├─► COVERAGE DIFF (site level): discovered vs ideal → missing/thin → site report + content briefs
  │
  ▼
CRAWLER: discover → prioritize (top-N) → crawl (Crawl4AI) → content-hash gate
  │                                                            └ unchanged → carry forward
  ▼
PROCESSOR (per page, scorers concurrent): 10-criterion score → Dual-Layer Gap (60% blueprint+rubric / 40% competitor)
  │
  ▼
RECOMMENDER: schema / entity / content edits  (+ missing-page briefs from Coverage Diff)
  │
  ▼
VALIDATION: edit-efficacy re-score (≤3 retry)  +  INDEPENDENT VALIDATOR (deterministic signals + Perplexity citation)
  │                                                  └─► cited wins → validated-wins → criteria-refinement proposals (human-gated)
  ▼
REPORTS: per-page (incl. independent verdict) + site-level (coverage + rollup)  →  Human Review
  │
UTILITIES: async Orchestrator · PostgreSQL (store + queue) · Observability · Error Sink · Weekly cron · force-IPv4
```

- **Storage:** PostgreSQL only — result store *and* queue. New tables in §2.9.
- **APIs/services:** Crawl4AI/Playwright (crawl), PageSpeed (load speed),
  Gemini-compatible LLM (depth refinement + L3 synthesis), Perplexity (citation
  test). All optional behind injectable seams with deterministic fallbacks.
- **CLI:** `audit-cycle`, `blueprint generate|show`, `coverage`, `site-report`,
  `refinements` join the v3 commands.

---

## 4. Production readiness review

| Dimension | Rating | Justification |
|---|---|---|
| **Reliability** | Strong | Generator/coverage are best-effort and isolated — never abort a crawl. Per-page Error Sink retained. External clients (Gemini/Perplexity/PSI) return `None` on failure; deterministic fallbacks everywhere. Retry/backoff unchanged. |
| **Scalability** | Strong | Postgres-queue + N workers unchanged. Scorers and the analysis loop parallelize. Blueprint generated once per run and cached/versioned (not per page). |
| **Performance** | Good | Parallel scorers help the LLM-refined criteria; analysis fan-out cuts wall-clock when an LLM is enabled. Content-hash gate avoids re-processing unchanged pages. Crawl remains the dominant cost (unchanged). |
| **Security** | Good | Secrets stay in env only (`AEO__*`, `DATABASE_URL`); the PSI key now travels in the `x-goog-api-key` header (not a `key=` query param) and failure logs carry only the HTTP status + exception type, so no key reaches a log sink even on a 4xx/5xx. New external calls are read-only GET/POST to vendor APIs. Inputs validated by the Pydantic contract; LLM output is re-validated (slug/vocab + seed-question dedupe/length/count caps), never trusted into the sitemap. Recursive discovery's in-memory graph is bounded against adversarial fan-out. |
| **Maintainability** | Strong | Same layering and seams; config-over-code extended (`framework.yaml`). 106 new offline tests; ruff + mypy clean on all new/changed modules. |
| **Observability** | Strong | New steps emit structured logs (`blueprint_pinned`, `coverage_diff_persisted`, `reference_architecture_skipped`, `audit_cycle_complete`) and `agent_traces` rows; `aeo trace` unchanged. |

**Verification status (this environment):** 346 offline tests pass (240 v3
baseline preserved + 106 new), `ruff check` clean, `mypy` clean on every new module
and every modified module (2 pre-existing notes remain in untouched cli helpers).
**Not verifiable here (need live creds/services):** real Crawl4AI crawls, a live
Postgres (the `0009` migration + repo SQL are exercised by the gated
`test_db_smoke.py` round-trips when a DB is present), live Gemini synthesis, and
the live Perplexity citation test. These run behind injectable seams and are
unit-tested with fakes; they go live the moment keys/DB are configured.

**Adversarial review pass.** A multi-agent review of the V4 diff (find →
independently verify each finding) confirmed 10 issues across SQL, security, and
robustness; all 10 are fixed and regression-tested:

| Severity | Finding | Fix |
|---|---|---|
| High | `recent_observations` joined scores by `page_id` only, attaching a wrong-run/version tier to a citation; INNER JOIN also dropped unscored cited pages | Correlate the score to the citation's own `run_id`; `LEFT JOIN` so unscored cited pages surface with empty tiers (proposer already skips per-criterion) |
| High | PSI API key leaked into logs on any non-2xx (`str(exc)` stringifies the `key=` URL) | Key sent via `x-goog-api-key` header (out of the URL); failure logs carry only status + exception type |
| Medium | `save_versioned` SELECT-then-INSERT race could violate `UNIQUE(topic, version)` | Per-topic `pg_advisory_xact_lock` serializes the bump within the txn |
| Medium | LLM `extra_seed_questions` merged via `model_copy` — bypassed dedupe/blank-strip, unbounded | Rebuild node via `model_validate` (runs the validator) + caps on slug count, questions/slug, and string length |
| Medium | `recursive_discover` frontier/inbound maps unbounded on adversarial fan-out | Cap inbound map, frontier length, and per-page link fan-out (generous multiples of the fetch budget) |
| Low | `set_refinement_status`/`save_refinement` could write a status the CHECK rejects | Validate against the allowed set in Python (clear `ValueError`) |
| Low | `coverage_diffs`/`site_reports` upserts bumped `created_at = NOW()` on refresh | Drop the bump — `created_at` is now first-insert time |
| Low | Redundant secondary index on the `UNIQUE(run_id)` column in both tables | Removed; the unique constraint already provides the index |
| Low | Synthesis prompt advertised config-driven `journey_stages` the closed Literal rejects | Prompt vocab sourced from `get_args(PageType/Intent/JourneyStage)` |
| Low | Empty/no-op `extra_seed_questions` mislabeled the blueprint as LLM-authored | Relabel gated on real enrichment (`merged_any`), not dict-presence |

---

## 5. Remaining risks, assumptions, dependencies

1. **`.env` reconciliation (action required).** The current `.env` targets the
   wrong DB and naming convention. Replace with this project's contract:
   `DATABASE_URL=postgresql://<user>:<pw>@localhost:5432/aeo`, `AEO__*`-prefixed
   settings (see `.env.example`). Crawl4AI — not FireCrawl — is the crawler, so the
   `FIRECRAWL_API_KEY` is unused here. Left untouched because it holds a live secret.
2. **No Gemini/Perplexity keys present.** L3 synthesis and the citation test run in
   deterministic-fallback mode until keys are set. The blueprint is still complete
   and the validator still gates on its deterministic checks — but the
   *headline* generative quality and the real-world citation signal are dormant
   until configured.
3. **Crawler discrepancy.** The v4 diagram says "FireCrawl"; the implementation
   keeps the working, tested Crawl4AI/Playwright path. Switching would break tested
   code for no functional gain; left as a documented decision. A FireCrawl backend
   could be added behind the existing crawl-client seam if desired.
4. **PEV-only framework.** `framework.yaml` is seeded for one topic (the v4 build
   sequence's directive). Generalizing = add a topic block; no code change. The
   taxonomy/criteria are provisional (Aayush's L2) pending standards research.
5. **force-IPv4 scope.** The httpx-based clients honor the flag; the Crawl4AI
   Chromium fetch is a browser-launch concern (a `--disable-ipv6`-style flag), out
   of scope for the transport seam and noted for the OCI rollout.
6. **Coverage matching is heuristic.** Token-overlap + page-type matching is
   deterministic and tested, but a hand-curated slug-alias map per topic would cut
   false negatives on idiosyncratic URL schemes. Left as a tuning lever.
