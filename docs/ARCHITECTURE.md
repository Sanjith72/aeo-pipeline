# Architecture

How the AEO crawler is put together and *why* it's shaped this way. For
deployment topology see [DEPLOYMENT.md](DEPLOYMENT.md); for the
rubric→implementation mapping and test/benchmark numbers see
[VALIDATION.md](VALIDATION.md).

---

## 1. Design principles

Five decisions drive everything else. They're listed first because the rest of
the document is really just their consequences.

**1. Deterministic-first scoring.** Every one of the ten criteria produces a
1–5 tier from parsed HTML alone. The local LLM is an *optional refinement* on
content depth, never a dependency. Turn it off and you
still get a complete, reproducible 0–50 score. This keeps results auditable
("why did this page score 3 on headings?" has an answer in the evidence dict,
not a model's mood) and keeps CI fully offline.

**2. Config, not code.** Thresholds, weights, vocabularies, regexes, authority
domains, entity lists — all live in `config/*.yaml`. Re-tuning the rubric is a
YAML edit and a re-score, not a deploy. The Python encodes *how* to measure;
the YAML encodes *what counts as good*.

**3. Pure functions at the core.** Extractors are `extract(html, soup, url) ->
dict` with no I/O. Scorers are `score(ctx) -> CriterionScore` reading a single
in-memory context. Pure functions are the reason 330+ tests run with no database,
browser, or network.

**4. One bad part never sinks the page.** A throwing extractor is caught and
recorded as `{"error": ...}`; a throwing scorer floors to the minimum tier with
`scored_by="error"`. A run of 500 pages doesn't abort because one page had
malformed JSON-LD.

**5. Postgres is the whole backend.** It's the result store *and* the job queue
(`FOR UPDATE SKIP LOCKED`). No Redis, no RabbitMQ, no broker to operate. Scaling
out is "run more workers against the same DB."

---

## 2. Module map

```
src/aeo/
  cli.py            typer entry point (`aeo`) — thin; delegates to pipeline
  settings.py       layered config: code defaults → YAML → env (AEO__*)
  logging.py        structlog setup (json or console)

  crawl/            getting HTML
    runner.py         fetch_many() — async, one browser reused across URLs
    client.py         Crawl4AI/Playwright wrapper
    politeness.py     per-host rate limiting
    retry.py          tenacity backoff policy
    fingerprint.py    content-hash short-circuit (skip unchanged pages)

  extract/          turning HTML into signals (12 pure extractors)
    __init__.py       DEFAULT_EXTRACTORS — the registered list, in order
    meta · headings · schema_jsonld · qa_blocks · stats · entities
    eeat · links · readability · render_mode · glossary · chunker
    pagespeed.py      async PageSpeed Insights fetch (separate — it's network)

  nlp/              the optional LLM
    llm.py            Ollama client; .enabled gates everything
    tone.py           promotional-tone analysis (feeds content_depth)

  scoring/          turning signals into tiers
    rubric.py         loads config/scoring.yaml into a Rubric object
    result.py         ScoreContext + tier math (clamp, thresholds, priority)
    scorers/          one module per criterion (8) + SCORERS registry
    aggregator.py     score_page() — runs all scorers, weights the total

  storage/          persistence + queue
    db.py             psycopg2 ThreadedConnectionPool
    migrate.py        ordered, idempotent SQL runner (schema_versions table)
    migrations/       0001_init · 0002_extractions_and_scores · 0003_jobs
    models.py         dataclasses crossing layer boundaries
    repos/            pages · runs · extractions · scores · jobs · targets

  pipeline/         wiring it all together
    stages.py         ExtractStage · ScoreStage · PersistStage
    orchestrator.py   Orchestrator — owns one run end to end
    worker.py         Worker — drains the queue (horizontal scale unit)

config/             scoring · extractors · entities · crawler · prioritization ·
                    best_practices  (the rubric-as-data)
tests/              332 offline tests + 3 engineered fixtures
```

The layering is strict and one-directional: `cli → pipeline → {crawl, extract,
scoring, storage}`, and those four lean on `utils` + `settings` + `models`.
Nothing in `extract/` or `scoring/` imports from `pipeline/` or `storage/`,
which is what lets them stay pure and testable in isolation.

---

## 3. Data flow

A URL's journey, from `aeo run` (or a dequeued job) to a row in
`rubric_scores_v2`:

```
            ┌─────────────────────────── Orchestrator.run_urls ───────────────────────────┐
            │                                                                              │
 URLs ──►  runs_repo.start         fetch_many (async, browser reused)                      │
            │                              │                                               │
            │                              ▼                                               │
            │                      _psi_batch (async, only if scoring + PSI key)           │
            │                              │                                               │
            │                              ▼   per page: _process_one                      │
            │      ┌───────────────────────────────────────────────────────────┐         │
            │      │ fetch failed? → persist page row, count failed, stop        │         │
            │      │ unchanged (fingerprint vs prior runs)? → copy fwd, stop     │         │
            │      │ else: ExtractStage → PersistStage.extraction                │         │
            │      │       (if scoring) ScoreStage → PersistStage.score          │         │
            │      └───────────────────────────────────────────────────────────┘         │
            │                              │                                               │
            └──────────────────────── runs_repo.finish ────────────────────────────────────┘
                                           │
                                           ▼
                  PostgreSQL: crawled_pages · extractions · rubric_scores_v2
```

The sequence matters in three places:

- **PageSpeed is batched before the per-page loop**, not fetched inside each
  extractor. It's the one network call in the scoring path, so it runs
  concurrently (bounded by a semaphore) over all successful pages at once. Skip
  it entirely when not scoring or when no API key is set — `load_speed` then
  falls back to a neutral tier.
- **The fingerprint check runs *before* the page is upserted for this run.** If
  it ran after, the row we just wrote would always match and every page would
  look unchanged. Comparing against prior runs only is the whole point. (See
  [orchestrator.py:9](../src/aeo/pipeline/orchestrator.py).)
- **Extract → persist extraction → score → persist score** is ordered so a
  crawl-only run (`do_score=False`, the `aeo crawl` command) stops cleanly after
  the extraction is durable, and `aeo score` can pick it up later.

### Inside ExtractStage: the fresh-soup rule

`ExtractStage.run` parses a **new** `BeautifulSoup` for *each* extractor:

```python
for name, fn in self._extractors:
    soup = parse(page.html)        # fresh tree, every time
    bundle.put(name, fn(page.html, soup, page.url))
```

This looks wasteful until you know that `utils.html.body_text(soup)` is
*destructive* — it decomposes `<script>`, `<style>`, `<nav>`, `<footer>` to get
clean article text. A shared soup would mean the first text-reading extractor
silently strips the nodes that `schema_jsonld`, `glossary`, and `links` need.
Re-parsing (~1–2 ms with lxml) is cheap insurance against an entire class of
order-dependent bugs. See §6 for why this isn't worth "optimizing" away.

### Inside ScoreStage: the context object

Every scorer receives one `ScoreContext(bundle, rubric, llm)` and nothing else
— no HTML, no network, no DB. A scorer that wants the LLM checks `ctx.llm is not
None and ctx.llm.enabled` and degrades gracefully when it's off. `run_all`
catches per-scorer exceptions and floors them, so the registry's hard contract
(ten keys, always present) holds even when one scorer breaks. The aggregator
then weights the ten tiers into a 0–50 total and a remediation
`priority_tier` (a *low* score is a *high* priority to fix).

---

## 4. Key design decisions

### Why deterministic-first instead of LLM-first

The legacy prototype asked an LLM to score four ad-hoc criteria. That's
non-reproducible (same page, different score), slow, hard to audit, and
impossible to test offline. Here the LLM only *refines*: it averages into
`content_depth` and can act as a disqualifier on `stats`. Everything is
explainable from the evidence dict, the rubric is the source of truth, and the
two LLM-touched criteria are clearly marked `scored_by="hybrid"` in storage so
you always know which scores leaned on the model.

### Why Postgres as the queue

A dedicated broker would be a second stateful system to deploy, monitor, and
back up — for a workload measured in pages-per-minute, not events-per-second.
`SELECT ... FOR UPDATE SKIP LOCKED` gives N workers safe, contention-free claim
of distinct jobs from one table. Retries, backoff, and dead-lettering are
columns (`attempts`, `max_attempts`, `run_after`), not broker features. One
system to run, and the queue state is queryable with plain SQL (`aeo status`).

### Why the fingerprint short-circuit

Re-crawling the same competitor set weekly, most pages haven't changed. When the
content hash matches a prior run, we clone the previous extraction + score
forward instead of re-running extraction and the LLM — the expensive parts. It
only kicks in when scoring (extraction alone is cheap enough to just redo), and
`copy_unchanged` requires *both* the extraction and score copies to succeed
before counting the page as a clean skip.

### Why repos and stages are separate seams

`PersistStage` is a thin, mockable wall in front of the repos. Tests drive the
pipeline with an in-memory persist and never touch a database. Repos own SQL and
nothing else; stages own orchestration and nothing else. The same stages run
identically whether invoked inline by `aeo run` or by a queue `Worker`, so there
is exactly one code path to reason about.

---

## 5. Extensibility

The system is built to grow along three axes. Each is deliberately a small,
local change.

**Add a scoring criterion** (the big one):
1. `config/scoring.yaml` — add the criterion's thresholds + weight.
2. `scoring/scorers/<name>.py` — write `score(ctx) -> CriterionScore`.
3. `scoring/scorers/__init__.py` — register it in `SCORERS`.
4. `storage/migrations/000N_*.sql` — add the column; `rubric_scores_v2` is
   indexed by the registry keys.
5. Add a fixture-backed test.

That's the *entire* surface. The aggregator, CLI, and pipeline need no changes —
they iterate the registry.

**Add an extractor:** write `extract(html, soup, url) -> dict`, append it to
`DEFAULT_EXTRACTORS` in `extract/__init__.py`. It gets a fresh soup and failure
isolation for free.

**Add a job kind:** handle it in `Worker._dispatch`. The queue, claim/retry/
backoff machinery, and scaling story are kind-agnostic.

This is why the registries (`DEFAULT_EXTRACTORS`, `SCORERS`) exist as explicit
ordered lists rather than auto-discovery magic: adding a part is a visible,
reviewable one-line diff, and the order is pinned for reproducibility.

---

## 6. Testing strategy

Full coverage table and counts live in [VALIDATION.md](VALIDATION.md); the shape
of the approach:

- **Offline by construction.** `conftest.py` disables the LLM and clears the
  settings/client/YAML caches before importing `aeo`. No test needs a DB,
  browser, or Ollama — purity at the core is what makes this possible.
- **Fixtures as an executable rubric spec.** Three hand-built pages land on known
  totals (strong = 44/50, weak = 18/50, glossary surfaces the `DefinedTerm`
  gap). If a scorer drifts, a fixture total moves and a test fails — the rubric
  is pinned in code.
- **Determinism is a tested property, not a hope.** A real flaky bug (set
  iteration over authority domains under hash randomization) was found and fixed
  with a longest-match helper, then verified stable across multiple
  `PYTHONHASHSEED` values. Tests assert *which* deterministic answer, not just
  "an answer."

### The deliberate non-optimization

Extraction parses the HTML up to 12 times per page (fresh soup per extractor,
§3). In production this is invisible: the headless-browser fetch dominates CPU
parse by ~100× (seconds vs. ~20 ms). "Parse once and share" was consciously
*not* done — it would trade a real correctness guarantee for a saving the
bottleneck makes irrelevant. Premature optimization, declined on purpose and
recorded so the next reader doesn't "fix" it.

---

## 7. Scaling & operations

Mechanics and cloud specifics are in [DEPLOYMENT.md](DEPLOYMENT.md). The model
in one paragraph: a job is one crawl batch for one target, so the browser is
reused across the batch's URLs; horizontal scale is `N` `Worker` processes
against one Postgres, colliding-free via `SKIP LOCKED`. The natural ceiling is
the database connection pool (`DB_POOL_*`) and crawl politeness
(`AEO__CRAWLER__CONCURRENCY`, per-host limits), not the queue. Inspect live state
with `aeo status` — it reads queue depth and run reports straight from SQL.

---

## 8. Maintenance

What this codebase will ask of whoever inherits it:

- **Re-tuning the rubric** is the expected routine change — edit `config/*.yaml`,
  re-run the fixture tests to see where totals move, re-score. No deploy.
- **Migrations are forward-only and idempotent**, ordered by filename, tracked
  in `schema_versions`. Adding a criterion column is a new `000N_*.sql`; never
  edit an applied migration.
- **`config/` is not in the wheel** (it sits at the repo root, beside `src/`).
  Containers must point `AEO__CONFIG_DIR` at it — already baked into the
  Dockerfile and compose file. This is the single most common deployment
  footgun; it's called out in [DEPLOYMENT.md](DEPLOYMENT.md) too.
- **The structured logs are the operational surface.** Every stage emits a
  structlog event (`run_start`, `extractor_failed`, `unchanged_skip`,
  `job_failed` with backoff, …). In production set `AEO__LOG_FORMAT=json` and
  watch those keys; floored scores (`scored_by="error"`) and `extractor_failed`
  warnings are your signal that a page shape changed and a parser needs
  attention.

---

## 9. v4: the topic layer (Reference Architecture)

v4 adds a **topic layer** above the page layer without disturbing it. The five
design principles still hold — the new blocks are deterministic-first, config-over-
code, pure at the core, failure-isolated, and Postgres-backed.

```
src/aeo/
  reference/
    blueprint.py          THE CONTRACT — Blueprint/SitemapNode/CoverageMap (Pydantic,
                          closed vocab, input-hash versioning, JSONB round-trip)
    framework.py          L2 guardrail+ceiling loader over config/framework.yaml
    competitor_patterns.py L1 empirical floor — pure aggregation over competitor bundles
    generator.py          L3 synthesis — deterministic floor + bounded LLM augmentation
    feedback.py           validated-wins — propose (human-gated) criteria-target nudges
  processor/
    coverage_diff.py      site-level gap: discovered vs ideal sitemap → missing/thin
  validation/
    independent.py        non-circular checks (TLDR/H1-question/JSON-LD) + Perplexity
  nlp/perplexity.py       citation client (injectable, deterministic fallback)
  report/site_builder.py  site-level report (coverage + per-page rollup)
  crawl/transport.py      force-IPv4 transport seam (OCI Ampere)
  pipeline/reference_arch.py  DB glue: build patterns → generate+pin blueprint → coverage
  storage/repos/          blueprints · coverage · feedback · site_reports
  storage/migrations/0009_v4_reference_architecture.sql
config/framework.yaml     the L2 topic taxonomy + criteria definitions
```

Three decisions worth recording:

- **The blueprint is versioned, not regenerated per run.** Regenerating every run
  would move the measuring stick. The generator hashes its *inputs*; the repo
  reuses the pinned version on identical inputs and bumps on change, and every run
  records the version it was measured against (`crawl_runs.blueprint_id`).

- **The Independent Validator is additive, not a replacement.** v3's re-score gate
  is retained as the *edit-efficacy* check (does the edit raise the deterministic
  score?). The new validator adds *non-circular* signals the recommender doesn't
  directly optimize — so a page can't "pass" by the recommender grading its own
  homework. The two together strictly dominate v3's single circular gate.

- **Parallelism is a performance change, never a behavior change.**
  `run_all_parallel` returns byte-identical output to the sequential path
  (scorers are pure over a read-only context; results re-ordered to the fixed
  registry order), and the analysis fan-out keeps each page Error-Sink isolated.

Full v3→v4 narrative, readiness review, and risks: [MIGRATION_V3_V4.md](MIGRATION_V3_V4.md).
