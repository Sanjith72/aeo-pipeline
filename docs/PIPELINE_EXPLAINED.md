# AEO Crawler — Complete Pipeline & Component Reference (V4)

> **Audience.** This document is written so an engineer can explain *exactly* how
> the product works to a team leader — every component, not just the headline
> blocks. It starts with a plain-English overview and an end-to-end "follow one
> URL through the system" trace, then documents each subsystem in depth
> (Sections 1–12), and closes with a data-flow map, glossary, run guide, and the
> design decisions behind the build.
>
> Source references use the form `path/file.py:LINE` so you can jump straight to
> the code. Everything here was read from the current source — it reflects the
> V4 state of the codebase, including the post-review fixes.

---

## Overview — the product in one paragraph

The **AEO Crawler** audits web pages for **Answer Engine Optimization (AEO)** —
how well content surfaces inside AI answer engines (ChatGPT, Perplexity, Google
AI Overviews, etc.). It crawls a site, extracts structural signals from each
page's HTML, and scores every page **1–5 on ten criteria (max 50)** using a
**deterministic-first** rubric: all ten criteria score from parsed HTML alone, so
you always get a complete, reproducible score even with every AI/LLM dependency
turned off. On top of that page-level scoring, **V4** adds a *topic* layer: it
builds a versioned **blueprint** (the "ideal site" for a topic), measures the real
site against it (**Coverage Diff** — which pages are missing/thin), proposes
concrete edits (**Recommender**), proves those edits actually help against
*non-circular* signals plus a real **Perplexity citation test** (**Independent
Validator**), and feeds proven wins back into the rubric through a **human-gated**
learning loop. PostgreSQL is both the job queue and the result store; there is no
external broker. The whole thing runs once-off per URL/site or on a **weekly audit
loop**.

---

## How the system is organized (mental model)

There are **two layers** and **two execution phases**. Keep these four words
straight and the rest of the document falls into place.

**Two layers of analysis:**

| Layer | Question it answers | Core blocks |
|---|---|---|
| **Page layer** (v1–v3) | "How good is *this page* for AEO?" | Crawl → Extract → Score → Gap → Recommend → Validate → Report |
| **Topic layer** (v4) | "Does the *whole site* cover the topic the way the ideal site would?" | Blueprint → Coverage Diff → Independent Validator → Site Report → Feedback loop |

**Two execution phases** (deliberately split so each can run alone):

1. **Crawl/Score phase** — `Orchestrator.run_urls` / `run_site` → produces
   `crawled_pages`, `page_extractions`, `rubric_scores_v2`.
   (`pipeline/orchestrator.py:103`, `:116`)
2. **Analysis phase** — `Orchestrator.analyze_run` → for each scored *client*
   page runs Gap → Validate → Independent-Validate → Report, isolated per page.
   (`pipeline/orchestrator.py:217`)

The **weekly audit loop** (`Orchestrator.audit_cycle`,
`pipeline/orchestrator.py:295`) simply chains: discover → prioritize → blueprint →
coverage → crawl/score → analyze → site report.

**Two entry shapes:**

- An explicit **URL list** (`run_urls`) — score exactly these pages.
- A **domain** (`run_site`) — discover the site, prioritize, and run the top-N.

### The module map (where everything lives)

```
src/aeo/
  cli.py            typer CLI — the operator surface (entry point: `aeo`)        → §1
  settings.py       layered config: defaults → YAML → AEO__* env                 → §1
  logging.py        structlog (console in dev, JSON in prod)                      → §1
  crawl/            discovery, Crawl4AI client, runner, politeness, retry,
                    fingerprint, prioritize, force-IPv4 transport                 → §2
  extract/          15 pure extractors (meta, schema, qa, stats, …) + registry    → §3, §4
  nlp/              LLM client (Ollama/cloud), tone, Perplexity citation probe    → §7
  scoring/
    rubric.py       loads config/scoring.yaml
    result.py       tier math (clamp, thresholds, weighted total, priority)
    aggregator.py   score_page() — sequential or parallel
    scorers/        one module per criterion (10) + run_all / run_all_parallel    → §5, §6
  processor/        Dual-Layer Gap Analysis + site-level Coverage Diff            → §8
  reference/        blueprint contract, framework (L2), competitor patterns (L1),
                    generator (L3), feedback loop, best-practice loader, intent   → §8
  recommender/      schema / entity / content edit generators                    → §9
  validation/       recommend→simulate→re-score→retry(≤3) + Independent Validator → §9
  report/           per-page report builder + renderer + site-level report        → §10
  obs/              agent traces + per-page Error Sink (page isolation)           → §10
  storage/          psycopg2 pool, migrations 0001–0009, 15 repositories          → §11
  pipeline/         Orchestrator (async), Worker (queue), stages, analysis, glue  → §12
config/             scoring · extractors · entities · crawler · prioritization ·
                    best_practices · framework  (config-over-code)                → §3,§6,§8,§12
```

### Five design principles you should be able to recite

1. **Deterministic-first.** Every score and every gap is computable from parsed
   HTML alone. The LLM (`nlp/llm.py`), Gemini synthesis, and Perplexity are
   *optional refinements* behind injectable seams, each with a deterministic
   fallback. Turn them all off → you still get a complete 0–50 score and a
   complete blueprint. (Verifiable: the whole test suite runs with the LLM
   disabled.)
2. **Config-over-code.** Thresholds, weights, vocabularies, the ideal-site
   taxonomy, and best-practice targets are YAML (`config/*.yaml`), not code.
   Tuning the rubric or the framework is an edit, not a deploy.
3. **Postgres as queue *and* store.** Jobs are claimed with `FOR UPDATE SKIP
   LOCKED`; results live in the same database. No Redis/RabbitMQ.
4. **Page isolation (Error Sink).** One bad page never aborts a run — it is
   wrapped in `page_guard` (`obs/error_sink.py`), the failure is recorded, and the
   loop continues. Runs end `succeeded` / `partial` / `failed` accordingly.
5. **Page layer vs topic layer separation.** The blueprint/coverage/feedback
   (topic) machinery is *best-effort and isolated* at the front of a site run
   (`pipeline/orchestrator.py:150`) — a generator or DB hiccup logs
   `reference_architecture_skipped` and the crawl proceeds.

---

## End-to-end walkthrough — follow one URL through the system

This is the single most useful thing to show a team lead: a concrete trace of one
URL from `aeo audit securin.io` to a finished report. Each step cites the function
that does the work.

### Phase A — Discover & prioritize (site entry only)

1. **`aeo audit securin.io -t Securin`** calls `Orchestrator.run_site`
   (`pipeline/orchestrator.py:116`).
2. **Discovery** (`crawl/discovery.py:discover`) tries the **sitemap** first
   (`robots.txt` → `Sitemap:` directives → walk sitemap indexes → collect
   same-site `<loc>`s), and falls back to a **recursive BFS** from the homepage
   that follows same-site `<a href>` links and counts inbound links per URL. The
   recursive frontier and inbound map are now **bounded** (caps proportional to
   `max_urls`) so a pathological/adversarial page can't exhaust memory.
3. **Prioritization** (`crawl/prioritize.py:prioritize`) scores each discovered
   URL by `base_weight` (page-type, from `config/prioritization.yaml`) × a
   `traffic_signal` derived from inbound links, ranks them, and marks the top-N
   `selected`. The **full** ranking is persisted to `page_priorities` for
   observability (`persist_ranking`), even the URLs that didn't make the cut.

### Phase B — Topic layer (best-effort, isolated)

4. **Blueprint** (`pipeline/reference_arch.py:generate_and_pin_blueprint`): build
   **L1 competitor patterns** from the latest competitor extractions, load the
   **L2 framework** (`config/framework.yaml`), synthesize the **L3 blueprint**
   (`reference/generator.py:generate_blueprint` — deterministic floor + optional
   guardrailed LLM augmentation), then `save_versioned` (reuse if the input hash
   is unchanged, else bump the version — protected by a per-topic Postgres
   advisory lock) and **pin** it to the run.
5. **Coverage Diff** (`compute_and_persist_coverage`): map the prioritized URLs to
   `DiscoveredPage`s and diff them against the blueprint's ideal sitemap
   (`processor/coverage_diff.py:coverage_diff`) → `coverage_pct`, **missing**
   nodes, **thin** clusters → persisted to `coverage_diffs` (one row per run).

### Phase C — Crawl, extract, score (per page)

6. **Fetch** (`crawl/runner.py:fetch_many`): the selected URLs are crawled with
   Crawl4AI/Playwright (one browser reused), returning `FetchedPage`s.
7. **PageSpeed batch** (`Orchestrator._psi_batch`,
   `pipeline/orchestrator.py:397`): if a PSI key is set, mobile PageSpeed scores
   are fetched concurrently (semaphore-bounded). The key now rides in the
   `x-goog-api-key` **header**, never the URL.
8. For each page, **`_process_one`** (`pipeline/orchestrator.py:361`):
   - **Fingerprint short-circuit** (`crawl/fingerprint.py:should_skip`): if the
     page's content hash matches a prior run, clone the prior extraction + score
     forward (`PersistStage.copy_unchanged`) and skip the expensive work.
   - Otherwise **persist the page** (`crawled_pages`), then **`ExtractStage.run`**
     (`pipeline/stages.py:38`) runs every registered extractor over a **fresh
     `BeautifulSoup` per extractor** (text extraction is destructive by design),
     producing one `ExtractionBundle` → persisted to `page_extractions`.
   - **`ScoreStage.run`** (`pipeline/stages.py:64`) calls `score_page`, which runs
     the **ten scorers** (sequentially, or concurrently when
     `AEO__SCORING__PARALLEL` is on) and writes a `PageScore` → `rubric_scores_v2`
     (`UNIQUE(page_id, run_id, rubric_version)`).
9. The run is finished `succeeded`/`partial` (`runs_repo.finish`).

### Phase D — Per-page analysis (the deliverable)

10. **`aeo analyze -r RUN`** → `Orchestrator.analyze_run`
    (`pipeline/orchestrator.py:217`). For every scored **client** page (optionally
    fanned out across a thread pool when `AEO__VALIDATION__ANALYSIS_CONCURRENCY >
    1`), `_analyze_one` wraps `analyze_page` in the **Error Sink**:
    - **Query intent** is classified (`reference.classify_intent`), and the best
      **competitor page** for that intent is selected.
    - **Dual-Layer Gap Analysis** (`processor/gap_analysis.py:analyze_gap`):
      compares the page's tiers against a target that is **60% best-practice +
      40% the best competitor** → per-criterion gaps → `page_gaps`.
    - **Validate** (`validation/validator.py:validate_page`): the Recommender
      proposes edits, `simulate.py` applies them to a synthetic page and
      re-scores; the loop retries up to **3** times, landing `improved` /
      `could_not_improve`. Recommendations + outcome → `recommendations`.
    - **Independent Validate** (v4, when enabled) — `validate_independent`
      (`validation/independent.py`): checks **non-circular** signals (liftable
      TL;DR < 50 words, H1-is-a-question, valid JSON-LD present) and, if a
      Perplexity client is configured, the **real-world citation test**. A
      citation outcome is logged to `citation_results`
      (`pipeline/analysis.py:_record_citation`).
    - **Report** (`report/builder.py:build_report`): assembles the per-page
      report (overview, criteria, gaps, recommendations, and the new independent-
      validation section) → `page_reports`.

### Phase E — Site report & learning (audit loop only)

11. **Site report** (`Orchestrator._build_and_persist_site_report`,
    `:323`): combines the pinned blueprint + coverage diff + per-page report
    rollup into a site-level deliverable → `site_reports`. New-page **briefs** are
    generated for the missing blueprint nodes.
12. **Validated-wins feedback** (`reference/feedback.py`, surfaced via `aeo
    refinements --propose`): pages that *provably get cited* are compared per
    criterion against pages that don't; where the cited cohort consistently
    out-tiers, a **proposal** to nudge that criterion's target is written to
    `criteria_refinements` with `status='proposed'`. **A human accepts/rejects —
    the system never auto-applies** (that would be circular validation one level
    up).

### The picture

```
            ┌─────────────────────────── aeo audit DOMAIN ───────────────────────────┐
            │                                                                          │
  DISCOVER ─┤  sitemap / recursive BFS  → URL inventory (+inbound graph)               │
            │            │                                                             │
PRIORITIZE ─┤  base_weight × traffic_signal → rank → top-N selected  → page_priorities │
            │            │                                                             │
   TOPIC   ─┤  L1 competitor patterns + L2 framework + L3 LLM → BLUEPRINT (versioned)  │
   LAYER    │            └→ COVERAGE DIFF (missing/thin)                → coverage_diffs│
            │                                                                          │
   CRAWL   ─┤  Crawl4AI fetch → [fingerprint skip?] → persist           → crawled_pages │
            │            │                                                             │
  EXTRACT  ─┤  15 pure extractors (fresh soup each)                     → page_extractions
            │            │                                                             │
   SCORE   ─┤  10 deterministic scorers (LLM-optional, parallel-opt.)   → rubric_scores_v2
            │            │                                                             │
  ANALYZE  ─┤  Gap(60/40) → Recommend → Simulate/Re-score (≤3) →                       │
            │  Independent-Validate (+Perplexity) → Report             → page_gaps,     │
            │            │                                                recommendations,
            │            │                                                page_reports,  │
            │            │                                                citation_results
            │            ▼                                                              │
   SITE    ─┤  SITE REPORT (coverage + blueprint + per-page rollup)    → site_reports   │
   REPORT   │                                                                          │
 FEEDBACK  ─┤  cited vs uncited per criterion → PROPOSE target nudge   → criteria_refinements (human-gated)
            └──────────────────────────────────────────────────────────────────────────┘
   Cross-cutting: Error Sink isolates every page · agent_traces records every step · Postgres is queue + store
```

---

The remaining sections document each subsystem in the order it appears above, then
the storage layer and the orchestration that ties it together. Appendices cover the
data-flow map, glossary, how to run it, and the design decisions.

## 1. Entry Points & Cross-Cutting Concerns — CLI, Settings, Logging

This section documents the operator-facing surface of the entire AEO Crawler: the `aeo` command-line interface (every command and option), the layered configuration model (defaults → YAML → env), and the structured-logging setup. Everything a human invokes or tunes from the outside lives in these four files.

| File | Role |
|------|------|
| `src/aeo/cli.py` | Typer app — every `aeo …` subcommand |
| `src/aeo/settings.py` | Pydantic-settings config model + 3-layer loader |
| `src/aeo/logging.py` | structlog configuration + `get_logger` |
| `src/aeo/__init__.py` | Package version marker |

---

### 1.0 Package version — `src/aeo/__init__.py`

The package file is a one-liner: it sets `__version__ = "0.2.0"` (`src/aeo/__init__.py:3`) and carries the module docstring `"AEO crawler & scoring pipeline."`. No logic, no exports — it just stamps the package version (consistent with the `AEOBot/0.2` user-agent default).

---

### 1.1 CLI — `src/aeo/cli.py`

The CLI is a **thin Typer layer** over the pipeline and repos. The module docstring (`cli.py:1-28`) is itself the command map. Every command first bootstraps logging, then delegates to `Orchestrator`, `Worker`, repos, or the report renderer.

#### Module wiring

- `app = typer.Typer(add_completion=False, help="AEO content crawler & rubric scorer.")` (`cli.py:50`) — the root command group. Shell completion is disabled.
- `log = get_logger(__name__)` (`cli.py:51`) — module logger.
- `main()` (`cli.py:451`) calls `app()`; the `__main__` guard (`cli.py:455`) wires `python -m aeo.cli`. This `main` is the console-script entry point referenced by packaging (`aeo` → `aeo.cli:main`).

Top-level imports (`cli.py:38-48`) eagerly pull in `configure`/`get_logger` (logging), `Orchestrator`/`Worker`/`enqueue_batch` (pipeline), `render_report` (report), `health_check` (DB), `apply_pending` (migrations), the `Target` model, and five repo modules (`jobs`, `reports`, `scores`, `targets`, `traces`). Heavier or v4-only modules (discovery, prioritize, reference framework/generator, blueprints repo, feedback) are imported **lazily inside the command bodies** to keep startup fast and avoid importing LLM/discovery stacks for commands that don't need them.

#### Private helpers

These back every command:

- **`_bootstrap() -> None`** (`cli.py:54-55`) — calls `configure()` (structlog). Invoked at the top of every command so logging is set up before any work.
- **`_collect_urls(urls, file) -> list[str]`** (`cli.py:58-68`) — merges positional URL args with a `--file` of newline-separated URLs. It reads the file UTF-8, strips each line, **drops blank lines and lines starting with `#`** (so URL files can carry comments), then **de-dupes while preserving order** (the `seen` set with the `or seen.add(u)` trick). If the result is empty it raises `typer.BadParameter("no URLs provided — pass URLs as arguments or --file")`. Side effect: reads the file from disk.
- **`_resolve_target(name) -> Target`** (`cli.py:71-76`) — looks up a target by name via `targets_repo.find(name)`. On miss it prints (red) `unknown target '<name>' — run \`aeo targets\` to list them` and raises `typer.Exit(code=1)`. DB read.
- **`_print(obj) -> None`** (`cli.py:79-80`) — pretty-prints any object as JSON to stdout: `json.dumps(obj, indent=2, default=str)`. `default=str` lets it serialize datetimes/Decimals/etc. This is the canonical machine-readable output for the `run`/`crawl`/`audit`/`analyze`/`status` commands.

#### Commands (root group)

| Command | Signature (key args/opts) | What it triggers | Prints |
|---------|---------------------------|------------------|--------|
| `migrate` | — | `apply_pending()` | `applied: <names>` or `migrations up to date` |
| `targets` | — | `targets_repo.list_all(kind)` for client+competitor | one line per target |
| `run` | `URLS… -f/--file -t/--target -l/--label --score/--no-score` | `Orchestrator().run_urls(...)` | run summary JSON |
| `crawl` | `URLS… -f -t -l` | `Orchestrator().run_urls(..., do_score=False)` | run summary JSON |
| `audit` | `DOMAIN -t -l --max-urls --score/--no-score --analyze` | `Orchestrator().run_site(...)` [+`analyze_run`] | run summary [+ analysis] JSON |
| `discover` | `DOMAIN --max-urls --top` | discovery + prioritize (no DB) | ranked URL table |
| `score` | `-r/--run-id` | `Orchestrator().score_run(run_id)` | `scored N page(s) …` |
| `analyze` | `-r/--run-id --no-persist` | `Orchestrator().analyze_run(...)` | analysis summary JSON |
| `enqueue` | `URLS… -f -t -l` | `enqueue_batch(...)` | `enqueued job <id>: N url(s) …` |
| `worker` | `--max-jobs --idle-sleep` | `Worker(...).run_forever(...)` | `processed N job(s)` |
| `status` | `-r/--run-id` | `health_check()`, `jobs_repo.stats()`, [`scores_repo.run_report`] | health/queue + optional report |
| `trace` | `PAGE_ID --json` | `traces_repo.for_page(page_id)` | agent journey or JSON |
| `report` | `TARGET -r/--run-id -p/--page-id --json` | `reports_repo.*` + `render_report` | rendered reports + tally |
| `audit-cycle` | `DOMAIN -t -l --max-urls` | `Orchestrator().audit_cycle(...)` | full-cycle result JSON |
| `coverage` | `-r/--run-id` | `coverage_repo.get(run_id)` | coverage diff summary |
| `site-report` | `-r/--run-id` | `site_reports_repo.for_run` + `render_site_report` | rendered site report |
| `refinements` | `--propose --status --accept --reject` | `feedback_repo.*`, `propose_criteria_refinements` | proposals/list |

Detailed behavior of each:

**`migrate`** (`cli.py:83-88`) — applies pending DB migrations via `apply_pending()` and prints the list applied (or `migrations up to date`). Side effect: mutates DB schema (migration table + DDL).

**`targets`** (`cli.py:91-97`) — iterates the two `kind`s `"client"` and `"competitor"`, calling `targets_repo.list_all(kind)`, printing `{kind:11} {name:18} {domain}`. DB read only.

**`run`** (`cli.py:100-113`) — the full pipeline on an explicit URL set: crawl → extract → score. Options: positional `urls`, `--file/-f` (Path), `--target/-t` (default `"Securin"`), `--label/-l`, and the boolean flag pair `--score/--no-score` (default **True**). Resolves the target, builds the URL list, then `asyncio.run(Orchestrator().run_urls(url_list, target=tgt, label=label, do_score=score))` and `_print(summary.as_dict())`. Side effects: network crawl, LLM calls (if enabled), DB writes (runs, crawled_pages, scores).

**`crawl`** (`cli.py:116-128`) — same as `run` but **always `do_score=False`** (crawl + extract only). The docstring tells operators to score later with `aeo score -r RUN_ID`. Same options minus `--score`.

**`audit`** (`cli.py:131-151`) — the "bare domain to per-page reports" path. Takes a positional `DOMAIN`/URL plus `-t`, `-l`, `--max-urls` (cap discovery before ranking), `--score/--no-score` (default True), and `--analyze` (default **False**). Runs `Orchestrator().run_site(domain, target=tgt, label=label, do_score=score, max_urls=max_urls)` = Site Discovery → Page Prioritization (top-N) → crawl → extract → score. If **both** `--analyze` and `--score` are set, it additionally runs `orch.analyze_run(summary.run_id)` and prints that. (Note: `--analyze --no-score` does nothing extra, because the analyze branch is gated on `score`.) Side effects: discovery network GETs, crawl, LLM, DB writes.

**`discover`** (`cli.py:153-173`) — **inspection only: no crawl, no DB writes.** Lazily imports `discover as discover_site` and the prioritizer (`PageInput`, `load_prioritization_cfg`, `prioritize`). Runs async discovery, loads the prioritization config, and scores each discovered URL by wrapping it in `PageInput(d.url, d.internal_links)`. Prints `discovered N url(s) via <source>; top_n = <cfg.top_n>` then a table. `--top N` shows the top N rows; otherwise it shows only `selected` rows. Each row: `{mark} {rank:>3}. {final_score:>8.2f}  {page_type:9} {url}` where `mark` is `*` for selected. Intended to let an operator tune `prioritization.top_n` before committing to a real audit. Side effect: discovery network GETs only.

**`score`** (`cli.py:176-181`) — `--run-id/-r` is **required** (`typer.Option(...)`). Runs `Orchestrator().score_run(run_id)` over every extracted-but-unscored page and prints `scored N page(s) for run <run_id>`. Side effects: LLM calls, DB score writes.

**`analyze`** (`cli.py:184-194`) — back half of the pipeline on a **scored** run: per page, Dual-Layer Gap Analysis → Recommender → Validation (≤3) → per-page report, each page Error-Sink isolated. Required `--run-id/-r`; `--no-persist` (default False) computes without writing to the DB. Calls `Orchestrator().analyze_run(run_id, persist=not no_persist)` and `_print`s the summary. Side effects: LLM/Perplexity calls; DB writes (recommendations, validations, reports) unless `--no-persist`.

**`enqueue`** (`cli.py:197-209`) — queues a crawl batch for a worker. Same URL/target/label options as `crawl`. It resolves (validates) the target before enqueuing but discards it, then `enqueue_batch(url_list, target, label)` and prints `enqueued job <job_id>: N url(s) for <target>`. Side effect: inserts a queued job row. (Note: `enqueue_batch` receives the target **name string**, not the resolved `Target` object.)

**`worker`** (`cli.py:212-220`) — drains the queue. `--max-jobs` (default None = run forever), `--idle-sleep` (default **5.0** seconds to wait when the queue is empty). Runs `Worker(idle_sleep=idle_sleep).run_forever(max_jobs=max_jobs)` and prints `processed N job(s)`. Long-running. Side effects: everything the pipeline does, plus job-row state transitions.

**`status`** (`cli.py:223-230`) — operator health check. Prints `database : ok` / `UNREACHABLE` from `health_check()`, then `queue    : <jobs_repo.stats()>`. If `--run-id/-r` is given, `_print`s `scores_repo.run_report(run_id)`. DB reads only.

**`trace`** (`cli.py:233-256`) — observability dump of a single page's agent journey. Positional `PAGE_ID` is `crawled_pages.id`; `--json` emits raw rows. Loads `traces_repo.for_page(page_id)`; if empty prints `no traces for page <id>`. Human-readable mode prints one line per traced step: `{ts}  {agent:12} {step:18} {status:8} {dur:>8}{model}` where `ts` is the first 19 chars of `created_at`, `dur` is `<ms>ms` or `-`, and `model` is `[<model>]` when present; error rows get an indented `! <error>` line. DB read only.

**`report`** (`cli.py:259-297`) — renders the final per-page AEO/SEO deliverable. **Scope precedence** (`cli.py:272-280`): `--page-id/-p` → `reports_repo.for_page`; else positional `TARGET` (resolved, then `reports_repo.for_target(tgt.id, tgt.kind, run_id=run_id)` — its latest run unless `--run-id` narrows it); else `--run-id/-r` alone → `reports_repo.for_run`. With none of the three it raises `typer.BadParameter("provide a TARGET name, --run-id, or --page-id")`. `--json` emits raw rows; otherwise it prints `render_report(row)` per row, then a tally line `N report(s): <count> <status>, …` built from each row's `review_status` (surfaces Human-Review status). DB reads only.

#### v4 Reference Architecture commands

**`audit-cycle`** (name `"audit-cycle"`, `cli.py:300-313`) — the **v4 Weekly Audit Loop** and the entry point the systemd timer/cron in `ops/` invokes weekly: discover → blueprint → coverage diff → crawl → score → analyze → site report. Args `DOMAIN`, `-t` (client), `-l`, `--max-urls`. Runs `asyncio.run(Orchestrator().audit_cycle(domain, target=tgt, label=label, max_urls=max_urls))` and `_print`s the result. Side effects: the union of discovery, crawl, scoring, analysis, blueprint generation/reuse, and coverage/site-report writes.

**`blueprint` sub-group** (`cli.py:316-318`) — its own `typer.Typer(add_completion=False, help="v4 Reference Architecture blueprints.")` mounted as `app.add_typer(blueprint_app, name="blueprint")`, giving `aeo blueprint generate` / `aeo blueprint show`.

- **`blueprint generate`** (`cli.py:321-345`) — `--topic` (default: the framework's topic), `--llm/--no-llm` (default **True**, use LLM for L3 synthesis). Lazily imports the LLM client, `build_competitor_patterns`, `load_framework`, `generate_blueprint`, and the blueprints repo. Loads the framework, resolves the topic, builds competitor patterns from `framework.required_entities` (**L1**), optionally gets the LLM client (**L3**), generates the blueprint (framework = **L2**), then `blueprints_repo.save_versioned(bp)`. Prints `blueprint topic=… version=… (reused|new) generator=… nodes=<sitemap len> competitors=<patterns.domains len>`. Side effects: competitor pattern computation (may hit DB/cache), LLM call (if enabled), blueprint row write (versioned, may reuse).
- **`blueprint show`** (`cli.py:348-376`) — `--topic`, `--version` (default: latest), `--json`. Resolves topic from the framework if unset, then `blueprints_repo.by_version(topic, version)` or `.latest(topic)`. If none: `no blueprint for topic '<t>' [vN]`. `--json` dumps `bp.to_jsonb()`. Otherwise prints `BLUEPRINT <topic> v<version> [<generator>] (<N> pages)`, one line per sitemap node `{priority:>4.2f}  [<page_type>/<intent>] <slug:32> <title>[  entities=…]`, and a `CLUSTERS` block (`{name:22} pillar=<pillar_slug> min_pages=<min_pages>`) when coverage clusters exist. DB read only.

**`coverage`** (`cli.py:378-396`) — required `--run-id/-r`. Lazily imports the coverage repo, `coverage_repo.get(run_id)`. If none: `no coverage diff for run <id>`. Prints a header `COVERAGE topic=… blueprint=v<…> <coverage_pct>% missing=<missing_count> thin=<thin_count>`, then up to **40** `MISSING` rows (`{priority:>4} [<page_type>] <slug> <title>`) and all `THIN` cluster rows (`<cluster>: <present_count>/<min_pages>`) from the `detail` JSON. DB read only.

**`site-report`** (name `"site-report"`, `cli.py:399-410`) — required `--run-id/-r`. Lazily imports `render_site_report` and the site-reports repo, fetches `site_reports_repo.for_run(run_id)`; if none, `no site report for run <id>`; else prints `render_site_report(row)` (coverage + per-page rollup). DB read only.

**`refinements`** (`cli.py:413-448`) — the human-gated **validated-wins** loop; "the system never auto-applies them." Flags: `--propose` (compute proposals from cited pages and save), `--status` (filter the listed proposals), `--accept <id>` / `--reject <id>` (set a refinement's status). Lazily imports `propose_criteria_refinements` and the feedback repo. Branch order:
  1. `--accept` → `feedback_repo.set_refinement_status(accept, "accepted")`, prints `refinement <id> accepted` (returns).
  2. `--reject` → same with `"rejected"` (returns).
  3. `--propose` → pulls `feedback_repo.recent_observations()`, runs `propose_criteria_refinements(observations)`, saves each via `feedback_repo.save_refinement(p)`, prints `proposed #<rid>: <criterion> <current_target>-><proposed_target>`; if none, `no refinement proposals (insufficient or inconclusive citation signal)` (returns).
  4. Default (no flags) → lists `feedback_repo.list_refinements(status)`, one line per row: `#<id> [<status>] <criterion> <current>-><proposed>  <rationale>`.
  Side effects: feedback-table reads/writes. The accept/reject/propose mutate the refinements table; status changes are how a human approves a proposed criterion-target change.

---

### 1.2 Settings — `src/aeo/settings.py`

#### The layered config model

Configuration merges in three layers, **later wins** (`settings.py:1-10`):

1. **Defaults in code** — the Pydantic model field defaults below.
2. **`config/*.yaml`** — currently `crawler.yaml` is merged into the `crawler` section (plus `load_yaml_file` for other static YAML like `scoring.yaml`, `entities.yaml`).
3. **Environment variables** — `AEO__SECTION__KEY=value` (and `.env`).

Secrets (DB URL, API keys) are env-only by policy (`settings.py:9`).

`PROJECT_ROOT` is `Path(__file__).resolve().parents[2]` (`settings.py:23`) and `DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"` (`settings.py:24`).

#### Env-nesting convention

The root `Settings` (`settings.py:160-180`) is a `pydantic_settings.BaseSettings` configured (`settings.py:161-167`) with:

```python
env_file=".env", env_file_encoding="utf-8",
env_nested_delimiter="__", env_prefix="AEO__", extra="ignore",
```

So a nested field is set via **`AEO__SECTION__KEY`** — e.g. `AEO__LLM__MODEL=qwen2.5:3b`, `AEO__CRAWLER__CONCURRENCY=8`. `extra="ignore"` means unknown env keys are silently dropped. `.env` is auto-loaded.

> Subtlety: pydantic-settings parses nested env at construction. The loader (`get_settings`) then **re-merges YAML over the crawler section and re-applies `AEO__CRAWLER__*` env on top** (`settings.py:201-209`) so that env still beats YAML even after the YAML overwrite. Other sections (`llm`, `validation`, …) rely on pydantic's native env parsing only.

#### The loader

- **`_load_yaml(path) -> dict`** (`settings.py:188-192`) — returns `{}` if the file is missing, else `yaml.safe_load` (or `{}` if the file is empty).
- **`get_settings() -> Settings`** (`settings.py:195-220`, `@lru_cache(maxsize=1)` — a process-wide singleton):
  1. Constructs `Settings()` (defaults + env + `.env`).
  2. Reads `config/crawler.yaml`; if non-empty, rebuilds `s.crawler = CrawlerCfg(**{**current_dump, **crawler_yaml})` — YAML keys win over defaults for that section.
  3. Re-applies `AEO__CRAWLER__*` env vars onto the merged crawler via `_set_nested` so env beats YAML.
  4. Honors the **legacy `DATABASE_URL` contract**: if `DATABASE_URL` is set, rebuilds `s.database` from it plus `DB_POOL_MIN`/`DB_POOL_MAX` (these three are **un-prefixed** env vars, distinct from the `AEO__DATABASE__*` style). Side effect: reads `os.environ` and the YAML file.
- **`_set_nested(obj, path, value)`** (`settings.py:223-239`) — walks a dotted attribute path and sets the leaf, **coercing the string** by the existing value's type: bool (`value.lower() in ("1","true","yes","on")`), int, float, else raw string. This is why `AEO__CRAWLER__RESPECT_ROBOTS=false` correctly becomes a Python `False`.
- **`load_yaml_file(name) -> dict`** (`settings.py:242-250`, `@cache`) — public helper for non-settings static YAML (scoring rubric, entity vocabularies). Loads `config/<name>` once per process; the docstring warns the result is shared and must be treated read-only (extractors call it on the hot path).

#### Every settings section and field

**`crawler` (`CrawlerCfg`, `settings.py:65-79`)** — values below are the code defaults; `config/crawler.yaml` overrides `user_agent` to `"AEOBot/0.2 (+https://securin.io/bot)"` and otherwise matches:

| Field | Default | Meaning |
|-------|---------|---------|
| `user_agent` | `"AEOBot/0.2"` | UA header for fetches |
| `concurrency` | `4` | async fan-out across hosts |
| `request_timeout_sec` | `30` | per-request timeout |
| `respect_robots` | `True` | obey robots.txt + crawl-delay |
| `force_ipv4` | `False` | bind client to `0.0.0.0` (AF_INET). For OCI Ampere/ARM where dual-stack AAAA resolution silently stalls fetches; off in dev/most clouds |
| `rate_limit` | `RateLimitCfg()` | token-bucket pacing (below) |
| `retry` | `RetryCfg()` | retry policy (below) |
| `fingerprint` | `FingerprintCfg()` | content-hash skip (below) |
| `browser` | `BrowserCfg()` | Crawl4AI tuning (below) |
| `discovery` | `DiscoveryCfg()` | site-discovery limits (below) |

- **`RateLimitCfg`** (`settings.py:32-34`): `requests_per_minute=30`, `burst=5` — polite per-host token bucket (separate from `concurrency`).
- **`RetryCfg`** (`settings.py:37-41`): `max_attempts=4`, `initial_backoff_sec=1.5`, `max_backoff_sec=30.0`, `retry_on_status=[408, 425, 429, 500, 502, 503, 504]` — exponential backoff between `1.5s` and `30s`; **only** these HTTP statuses are retried, anything else is treated as permanent.
- **`FingerprintCfg`** (`settings.py:44-46`): `enabled=True`, `algorithm="sha256"` — when a page's content hash equals the last run's, extraction + scoring are skipped (incremental re-crawl optimization).
- **`BrowserCfg`** (`settings.py:49-52`): `headless=True`, `remove_overlay_elements=True` (dismiss cookie/consent overlays), `word_count_threshold=0` (don't drop short text blocks) — passed to Crawl4AI.
- **`DiscoveryCfg`** (`settings.py:55-62`): `max_urls=200` (cap the inventory **before** prioritization, which then cuts to `prioritization.top_n`), `max_depth=2` (recursive BFS depth from the homepage when no sitemap), `max_sitemaps=50` (cap sitemap-index expansion against pathological sites), `timeout_sec=15`. Discovery uses plain HTTP GETs (no JS render needed to read links).

**`llm` (`LLMCfg`, `settings.py:82-96`)** — the scoring/synthesis LLM:

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `True` | off → scorers fall back to deterministic-only |
| `provider` | `"ollama"` | `"ollama"` (local) or `"cloud"` (OpenAI-compatible) |
| `host` | `"http://localhost:11434"` | Ollama endpoint |
| `model` | `"qwen2.5:3b"` | Ollama model |
| `cloud_base_url` | `"https://generativelanguage.googleapis.com/v1beta/openai"` | any OpenAI-compatible `/chat/completions` (Gemini compat, OpenAI, Together…) |
| `cloud_model` | `"gemini-2.5-flash"` | cloud model |
| `cloud_api_key` | `None` | set via `AEO__LLM__CLOUD_API_KEY` |
| `timeout_sec` | `120` | generation timeout |
| `temperature` | `0.1` | near-deterministic generation |
| `num_predict` | `600` | max generated tokens |

**`database` (`DatabaseCfg`, `settings.py:99-102`)**: `url="postgresql://aeo:aeo@localhost:5432/aeo"`, `pool_min=2`, `pool_max=10`. In production the loader overrides `url` from the legacy `DATABASE_URL` env var (with `DB_POOL_MIN`/`DB_POOL_MAX`).

**`validation` (`ValidationCfg`, `settings.py:105-118`)**:

| Field | Default | Meaning |
|-------|---------|---------|
| `max_attempts` | `3` | Validation loop retries (apply edits to a synthetic page, re-score, retry Recommender if no improvement). After the cap, the page is flagged `could-not-improve` and routed to Human Review — so a stubborn page can't spin forever |
| `independent_enabled` | `True` | v4 Independent Validator: after the edit-efficacy gate, run non-circular signals (deterministic checks + Perplexity citation test) to decide review routing. Off → only the v3 circular re-score gate decides |
| `analysis_concurrency` | `1` | fan-out for the per-page analysis loop (gap→recommend→validate→report). `1` = sequential (v3); higher pays off when an LLM is enabled (pages are independent + Error-Sink isolated) |

**`perplexity` (`PerplexityCfg`, `settings.py:121-130`)** — the Independent Validator's real-world citation signal:

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `False` | off by default; with no key the validator falls back to deterministic checks and **never fails a page for a missing key** |
| `api_key` | `None` | Perplexity key |
| `base_url` | `"https://api.perplexity.ai"` | endpoint |
| `model` | `"sonar"` | Perplexity model |
| `timeout_sec` | `60` | request timeout |

**`scoring` (`ScoringCfg`, `settings.py:133-138`)** — the v4 Parallel Processor: `parallel=False`, `max_workers=8`. When `parallel` is on, criterion scorers run in a thread pool; output is identical to sequential because the scorers are pure over a shared read-only context — the win is on I/O-bound LLM-refined criteria.

**`reference_architecture` (`ReferenceArchitectureCfg`, `settings.py:141-152`)** — the versioned per-topic ideal-site blueprint:

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `True` | turn the Reference Architecture on |
| `topic` | `"PEV"` | default topic — Proactive Exposure / Vulnerability management, for Securin |
| `framework_version` | `"1"` | framework file version to load |
| `min_pages_per_cluster` | `10` | topical-authority target = thin-cluster threshold |
| `regenerate_cadence_days` | `30` | regenerate only this often; else reuse the pinned version so the measuring stick doesn't move week-to-week |

The generator uses the configured LLM (set `llm.provider=cloud` at Gemini's OpenAI-compatible endpoint) for synthesis, falling back to the deterministic builder when the LLM is disabled or fails.

**Root-level scalar fields (`Settings`, `settings.py:177-180`)**:

| Field | Default | Meaning |
|-------|---------|---------|
| `log_level` | `"INFO"` | structlog level (consumed by `logging.configure`) |
| `log_format` | `"console"` | `"console"` (dev pretty) or `"json"` (prod) |
| `config_dir` | `str(DEFAULT_CONFIG_DIR)` (`<root>/config`) | where YAML files live; the loader reads `crawler.yaml` and `load_yaml_file` reads others from here |
| `psi_api_key` | `None` | Google PageSpeed Insights API key (env-only, e.g. `AEO__PSI_API_KEY`) for the SEO/perf signal |

---

### 1.3 Logging — `src/aeo/logging.py`

structlog setup: console-pretty in dev, JSON in prod. Usage pattern (`logging.py:4-8`):

```python
from aeo.logging import get_logger
log = get_logger(__name__)
log.info("crawl_start", url=url, batch=batch_id)
```

A module-level `_configured = False` flag (`logging.py:19`) makes setup idempotent.

- **`configure() -> None`** (`logging.py:22-54`) — sets up structlog once.
  - Returns immediately if already configured (`logging.py:23-25`).
  - Reads `get_settings()`; maps `s.log_level` (default `INFO`) to a `logging` level via `getattr(logging, s.log_level.upper(), logging.INFO)` — an unknown level silently defaults to INFO.
  - **Shared processor chain** (`logging.py:30-36`): `merge_contextvars` (binds `bind_contextvars` ambient context — e.g. run/page ids), `add_log_level`, `TimeStamper(fmt="iso", utc=True)` (ISO-8601 UTC timestamps), `StackInfoRenderer`, `format_exc_info` (renders exceptions).
  - **Renderer selection** (`logging.py:38-41`): if `s.log_format == "json"` → `JSONRenderer()` (machine-readable for prod log shippers); otherwise → `ConsoleRenderer(colors=True)` (human dev output).
  - `structlog.configure(...)` (`logging.py:43-48`): processors = shared + renderer; `wrapper_class=make_filtering_bound_logger(level)` (filters below the configured level cheaply); `logger_factory=PrintLoggerFactory(file=sys.stderr)` — **all logs go to stderr**, leaving stdout clean for the CLI's JSON output (`_print`); `cache_logger_on_first_use=True`.
  - **Tames noisy libraries** (`logging.py:51-52`): sets `urllib3`, `asyncio`, and `playwright` stdlib loggers to `WARNING`.
  - Sets `_configured = True`.
  - Side effect: mutates global structlog + stdlib-logging state.
- **`get_logger(name=None) -> structlog.stdlib.BoundLogger`** (`logging.py:57-60`) — lazily calls `configure()` if not yet configured, then returns `structlog.get_logger(name)`. This is the single import every module uses; because `configure()` is idempotent and self-bootstrapping, callers never have to order setup themselves (the CLI's `_bootstrap()` calling `configure()` explicitly is belt-and-suspenders).

**Design intent.** The log destination (stderr) and the data destination (stdout) are deliberately separated so an operator can pipe `aeo run … | jq` on the JSON summary while still seeing structured logs. `console` vs `json` is the single dev↔prod toggle (`AEO__LOG_FORMAT=json`), and contextvars let the pipeline attach run/page identifiers once and have them appear on every downstream event without threading them through call signatures.

## 2. Crawl Subsystem — Discovery, Fetching, Politeness, Prioritization

The crawl subsystem turns *a domain* into *a ranked, fetched set of pages* the
per-page optimization loop will work on. It splits cleanly into two phases that
use **different network stacks on purpose**:

1. **Discovery + prioritization** — cheap, JS-free `httpx` GETs that harvest the
   URL inventory (sitemaps / recursive BFS), build an inbound-link graph, score
   every URL, and cut to a top-N.
2. **Fetching** — the expensive Crawl4AI/Playwright render, run **only** on the
   selected top-N, behind a semaphore, a per-host rate limiter, robots.txt, retry,
   and a content-hash short-circuit.

The package docstring (`src/aeo/crawl/__init__.py:1`) sums it up: *"Crawling layer:
Crawl4AI wrapper + politeness + concurrency."* All settings come from
`config/crawler.yaml` (section `crawler` in `settings.py`) and
`config/prioritization.yaml`; every value here is overridable by `AEO__CRAWLER__*`
environment variables.

A handful of shared URL helpers from `src/aeo/utils/url.py` are used throughout:
- `normalize(url)` — lowercases scheme+host, strips default ports, drops the
  fragment, normalizes the trailing slash (`url.py:8`).
- `same_site(a, b)` — compares *registrable* domains, so `www.x.com` and `x.com`
  count as the same site (`url.py:25`).
- `absolute(base, href)` — `urljoin` (`url.py:29`).
- `host_of(url)` — lowercased hostname, the per-host key for rate limiting and
  robots (`url.py:21`).

---

### 2.1 Site discovery — `src/aeo/crawl/discovery.py`

**Design intent (module docstring, `discovery.py:1-23`).** Two strategies tried
in order: **sitemap** (authoritative, cheap — read `robots.txt` `Sitemap:`
directives, fall back to `/sitemap.xml`, walk sitemap *indexes* to their children,
collect every same-site `<loc>`) and **recursive** BFS (when no sitemap exists,
crawl same-site `<a href>` links from the homepage, recording the internal-link
graph so each URL carries an inbound-link count). Discovery uses plain HTTP GETs,
**not** the rendering crawler, because harvesting links/sitemaps needs no
JavaScript — the expensive Crawl4AI render is reserved for the selected top-N.

The inbound-link count becomes the prioritizer's `traffic_signal`. **Sitemap URLs
have no graph**, so they later rank on `base_weight` alone (floored to
`min_traffic_signal`); recursively discovered URLs carry a real count. Every
network call routes through an injectable `fetch` callable so the logic is
unit-testable with no network.

#### Bounded-frontier / inbound caps (`discovery.py:42-49`)

Recursive discovery walks an *external, untrusted* domain, so the in-memory
working set is bounded **independently of the fetch budget** — one adversarial
page can emit millions of links. These caps are generous multiples of the fetch
budget (no effect on real sites) but stop the frontier/inbound graph growing
without limit before the budget halts the BFS:

| Constant | Value | Meaning |
|---|---|---|
| `_FRONTIER_CAP_FACTOR` | `4` | frontier length ≤ `max_urls × 4` |
| `_INBOUND_CAP_FACTOR` | `10` | distinct inbound URLs tracked ≤ `max_urls × 10` |
| `_MAX_LINKS_PER_PAGE` | `500` | links processed from any single page |

With the default `max_urls=200`, that's a frontier cap of 800 and an inbound-graph
cap of 2000 distinct URLs.

#### Data types

- `DiscoveredUrl(url: str, internal_links: int = 0)` — one URL plus its inbound
  count (`discovery.py:52`).
- `DiscoveryResult(domain, source, urls)` — `source` ∈ `{"sitemap", "recursive",
  "seed"}`; `len()` returns the URL count (`discovery.py:58`).
- `SitemapDoc(is_index: bool, locs: list[str])` — `is_index` distinguishes a
  sitemap *index* (child sitemaps in `locs`) from a `urlset` (page locs)
  (`discovery.py:68`).
- `FetchText = Callable[[str], Awaitable[str | None]]` — the injectable fetcher
  contract: return the body text, or `None` on any failure / non-200
  (`discovery.py:40`).

#### Pure parsers (no network)

- **`parse_robots_sitemaps(text) -> list[str]`** (`discovery.py:79`). Scans
  `robots.txt` line by line, partitions on the first `:`, and collects the value
  of every line whose key (case-insensitively) is `sitemap`. Returns the directive
  URLs in file order.
- **`_localname(tag) -> str`** (`discovery.py:91`). Strips an XML namespace:
  `{http://…}loc` → `loc`, so parsing is namespace-tolerant.
- **`parse_sitemap(text) -> SitemapDoc`** (`discovery.py:96`). `ET.fromstring` the
  XML; on `ParseError` returns an empty non-index doc (malformed → nothing).
  `is_index` is true when the root local-name is `sitemapindex`. Collects every
  element with local-name `loc` that has non-empty stripped text. So an index
  yields child-sitemap URLs and a urlset yields page URLs, both in `locs`.
- **`select_same_site(urls, domain) -> list[str]`** (`discovery.py:112`).
  Normalizes each URL, keeps only same-site ones, dedupes preserving first-seen
  order. The single chokepoint that enforces "stay on this site."
- **`seed_url(domain) -> str`** (`discovery.py:127`). A bare domain becomes
  `https://domain/`; a full URL passes through normalized — so callers can hand
  either form.

#### Network fetcher

**`_default_fetch_text(url) -> str | None`** (`discovery.py:141`). The default
`FetchText`. Builds an `httpx.AsyncClient` with `timeout = cfg.discovery.timeout_sec`
(15s), `follow_redirects=True`, `User-Agent = cfg.user_agent`, and
**`transport=async_transport()`** — the force-IPv4 seam (§2.9). Returns
`resp.text` on HTTP 200; logs `discovery_fetch_non200` for other statuses and
`discovery_fetch_failed` on exceptions, returning `None` either way. The
swallow-and-return-`None` contract is what lets the BFS skip dead links without
aborting.

#### `gather_sitemap_urls(domain, *, fetch=None, max_urls=None, max_docs=None) -> list[str]`

(`discovery.py:161`.) Collects same-site page URLs from the domain's sitemaps.

**Algorithm.** Defaults: `fetch = _default_fetch_text`, `max_urls =
cfg.max_urls` (200), `max_docs = cfg.max_sitemaps` (50). Fetch `/robots.txt`;
seed the work queue with its `Sitemap:` directives, or `[/sitemap.xml]` if none.
Then loop while `queue` is non-empty **and** `fetched < max_docs` **and**
`len(pages) < max_urls`:
1. Pop and `normalize` the next sitemap URL; skip if already in `seen_docs`.
2. Fetch it (`fetched += 1`); skip on empty body.
3. `parse_sitemap`. If it's an **index**, append its same-site child sitemaps to
   the queue; otherwise extend `pages` with its locs.

Finally `select_same_site(pages, seed)[:max_urls]` — dedupe and truncate. The two
caps (`max_docs`, `max_urls`) bound work against pathological sitemap fan-out.

#### `recursive_discover(domain, *, fetch=None, max_urls=None, max_depth=None) -> dict[str, int]`

(`discovery.py:202`.) BFS from the homepage following same-site links; returns
`{url: inbound_count}` over the discovered set, capped to `max_urls` (highest
inbound first).

**Algorithm.** Defaults: `max_urls = cfg.max_urls` (200), `max_depth =
cfg.max_depth` (2). `bs4`/HTML parsing is imported lazily inside the function to
keep it off the module-load path. State: `inbound = {seed: 0}`, `visited = set()`,
`frontier = [(seed, 0)]`, plus `inbound_cap = max_urls × 10` and `frontier_cap =
max_urls × 4`. Loop while `frontier` and `len(visited) < max_urls`:
1. Pop `(url, depth)`; skip if visited; mark visited.
2. Fetch HTML; skip on empty (sets `fetched_any = True` once any page returns).
3. Parse, extract `internal` links via `extract.links.extract`, take the first
   `_MAX_LINKS_PER_PAGE` (500).
4. For each link: `normalize`, drop if not `same_site`. If already known, `inbound
   += 1`; else if `len(inbound) < inbound_cap`, register with count 1; else
   **skip** (working set saturated → stop tracking novel URLs).
5. Enqueue `(norm, depth+1)` only if not visited **and** `depth < max_depth`
   **and** `len(frontier) < frontier_cap`.

**Edge case:** if nothing was ever fetched (`fetched_any == False`, e.g. homepage
unreachable) it returns `{}` so the caller falls through to the seed-only result
rather than emitting a fake homepage. Otherwise it ranks `inbound.items()` by
`(-count, url)` (count desc, URL asc for determinism) and returns the top
`max_urls` as a dict.

#### `discover(domain, *, fetch=None, max_urls=None, prefer_recursive=False) -> DiscoveryResult`

(`discovery.py:260`.) The public entry point and the strategy ladder:
1. Unless `prefer_recursive`, try `gather_sitemap_urls`; if non-empty, return
   `source="sitemap"` (logs `discovery_sitemap`).
2. Else `recursive_discover`; if non-empty, return `source="recursive"` with each
   URL's inbound count (logs `discovery_recursive`).
3. Else last-resort: a single `DiscoveredUrl(seed)`, `source="seed"` (logs
   `discovery_seed_only`) — **the pipeline always has at least the homepage to
   run**.

`prefer_recursive=True` skips the sitemap path entirely (useful when a site has a
stale/garbage sitemap and you want the live link graph).

---

### 2.2 Crawl4AI / Playwright client — `src/aeo/crawl/client.py`

**Design intent (docstring, `client.py:1-8`).** One browser per `CrawlClient`
instance; reuse saves ~1.5 s of browser startup per page. Used as an async context
manager:
```python
async with CrawlClient() as client:
    page = await client.fetch(url)
```

**Lazy Crawl4AI import (`client.py:26-36`).** Module-level `_AsyncWebCrawler` /
`_CrawlerRunConfig` start as `None`; `_import_crawl4ai()` imports `crawl4ai`
(which pulls in Playwright) only on first use, so tests and the discovery path
never pay that import cost.

#### `class CrawlClient`

- **`__init__`** (`client.py:40`). Holds a `RobotsCache`, a `RateLimiter`, and a
  cached `self._cfg = get_settings().crawler`. No browser yet.
- **`__aenter__`** (`client.py:46`). Calls `_import_crawl4ai()`, constructs
  `_AsyncWebCrawler(verbose=False, headless=cfg.browser.headless)` (default
  headless `true`), enters it. **Side effect: launches a Playwright browser.**
- **`__aexit__`** (`client.py:52`). Closes the browser and nulls the handle.
- **`fetch(url) -> FetchedPage`** (`client.py:57`). The politeness front door:
  1. `normalize(url)`.
  2. **robots check** — `self._robots.allowed(url, cfg.user_agent)`. If
     disallowed: log `robots_disallowed` and return a failed `FetchedPage` with
     `error="blocked_by_robots_txt"` (no network fetch happens).
  3. **rate limit** — `await self._limiter.acquire(url)` (blocks until a per-host
     token is available).
  4. Delegate to `_fetch_once`.
- **`_fetch_once(url, url_normalized) -> FetchedPage`** (`client.py:72`). Builds a
  `CrawlerRunConfig(only_text=False, remove_overlay_elements=
  cfg.browser.remove_overlay_elements, word_count_threshold=
  cfg.browser.word_count_threshold)` and runs `crawler.arun(...)` inside
  `asyncio.wait_for(timeout=cfg.request_timeout_sec)` (30 s). On `TimeoutError`:
  return a failed page with `error="timeout_after_{N}s"`. On **any other
  exception**: re-raise as `TransientError` — the deliberate "treat unknown
  failures as transient unless caller knows better" policy that drives the retry
  loop. Timing comes from a `stopwatch()` context manager (`fetch_duration_ms`).

#### `_to_fetched(result, url, url_normalized, ms) -> FetchedPage` (`client.py:99`)

Adapts the heterogeneous Crawl4AI result into the canonical `FetchedPage` model,
defensively because Crawl4AI's shape varies across versions:
- `success` from `result.success` (default `True`).
- `html` from `result.html` or `result.cleaned_html` (first non-empty).
- `markdown`: if `result.markdown` is a `str` use it; otherwise prefer
  `fit_markdown`, then `raw_markdown`, then `str(...)`.
- `metadata` coerced to a dict; `title` from `title`/`ogTitle` (truncated to
  **512 chars**), `meta_description` from `description`/`ogDescription`.
- `http_status` from `metadata.statusCode` or `result.status_code`;
  `error` from `result.error_message` only when not successful.
- **`content_hash = content_hash(html)` when HTML is present** (`None`
  otherwise) — this is what feeds the unchanged-content short-circuit (§2.6).
  `content_hash` (`utils/hashing.py:10`) SHA-256s *whitespace-collapsed* HTML, so
  pure whitespace churn does not change the hash.

---

### 2.3 Crawl runner — `src/aeo/crawl/runner.py`

**Design intent (docstring, `runner.py:1-5`).** Bounded-concurrency batch crawl:
one shared `CrawlClient` (so the browser is reused) gated by a semaphore, with
per-host politeness layered on top.

#### `fetch_many(urls: Iterable[str]) -> list[FetchedPage]` (`runner.py:22`)

Fans out a URL list and returns results **in input order**.

**Algorithm.** `sem = asyncio.Semaphore(cfg.concurrency)` (default 4). Open one
`CrawlClient`. Define `_one(idx, url)`:
- Acquire the semaphore.
- Drive `client.fetch(url)` through the retry loop: `async for attempt in
  fetch_retry(): with attempt: page = await client.fetch(url)` (Tenacity, §2.5).
- On success, log `crawl_ok` (url, http, ms, ok) and return `(idx, page)`.
- On exhausting retries / any exception, log `crawl_failed` and return a failed
  `FetchedPage(success=False, error=str(exc))` so **one bad URL never sinks the
  batch**.

`asyncio.gather` all `_one(...)`, then sort by `idx` to restore input order.
Net effect: up to `concurrency` pages render concurrently, each still bounded
per-host by the rate limiter inside `client.fetch`.

---

### 2.4 Politeness — `src/aeo/crawl/politeness.py`

**Design intent (docstring, `politeness.py:1-6`).** Robots awareness + a per-host
token-bucket rate limiter. Both cheap, both prevent bans at scale. The robots
cache is per-process; the buckets are per-host, per-process.

#### `class RobotsCache` (`politeness.py:27`)

- **`allowed(url, user_agent) -> bool`** (`politeness.py:32`). If
  `cfg.respect_robots` is `False`, short-circuits to `True` (robots ignored).
  Otherwise keys by `host_of(url)`, lazily loads & caches a `RobotFileParser`
  under an `asyncio.Lock` (so concurrent coroutines fetch a host's robots once),
  and returns `rp.can_fetch(user_agent, url)`.
- **`_load(host)`** (`politeness.py:43`). GETs `https://{host}/robots.txt`
  (`httpx`, 10 s timeout, follow redirects). HTTP 200 → `rp.parse(lines)`.
  **Anything else, or any exception, → `rp.parse([])` which means "allow
  everything"** — a missing/broken robots.txt is treated as permissive, and the
  failure is logged `robots_fetch_failed`. Note: this loader does **not** use the
  force-IPv4 transport (it's a hardcoded `AsyncClient`).

#### Token bucket — `class RateLimiter` (`politeness.py:70`)

- **`__init__`** reads `cfg.rate_limit`: `capacity = burst` (5), `refill =
  requests_per_minute / 60` (30/60 = **0.5 tokens/sec**, i.e. one request every
  2 s sustained). Buckets are kept in a dict keyed by host, guarded by a lock.
- **`acquire(url)`** (`politeness.py:78`). Standard token bucket: on first contact
  with a host, create a full bucket (`tokens = capacity`). On each call, refill by
  `elapsed × refill_per_sec` (clamped to capacity), and if `tokens >= 1` consume
  one and return immediately. Otherwise compute exactly how long until one token
  accrues — `(1 - tokens) / refill_per_sec` — `await asyncio.sleep` that long, and
  retry. The bucket math runs under the lock; the sleep happens **outside** it so
  other hosts aren't blocked.

The `burst=5` capacity lets a fresh host take 5 requests immediately, then settles
to the 0.5 req/s sustained rate.

#### `_Bucket` dataclass (`politeness.py:62`)

Plain mutable state: `capacity`, `tokens` (float), `refill_per_sec`, `last`
(monotonic timestamp of last refill).

---

### 2.5 Retry / backoff — `src/aeo/crawl/retry.py`

**Design intent (docstring, `retry.py:1`).** A single Tenacity policy shared by
crawl + HTTP.

- **`fetch_retry() -> AsyncRetrying`** (`retry.py:15`). Reads `cfg.retry` and
  builds:
  - `stop = stop_after_attempt(max_attempts)` — **4** attempts.
  - `wait = wait_random_exponential(multiplier=initial_backoff_sec,
    max=max_backoff_sec)` — randomized exponential backoff seeded at **1.5 s**,
    capped at **30 s** (jitter avoids thundering-herd retries).
  - `retry = retry_if_exception_type(_RETRYABLE)`.
  - `reraise=True` — after the last failed attempt the original exception
    propagates (so `fetch_many` can log `crawl_failed` and build the error page).
- **`class TransientError(Exception)`** (`retry.py:30`) and `_RETRYABLE =
  (TransientError,)` (`retry.py:34`). **Only `TransientError` is retried.** This
  is the contract `CrawlClient._fetch_once` honors: it wraps unknown fetch
  exceptions as `TransientError`, while genuinely permanent problems (parse errors,
  bad URLs) raise other types and fail fast.

**Config note / discrepancy worth flagging.** `config/crawler.yaml:21` defines
`retry.retry_on_status: [408, 425, 429, 500, 502, 503, 504]` (and `RetryCfg`
carries the same default, `settings.py:41`), but the crawl client does **not**
consult HTTP status codes — its retry decision is purely exception-type based.
The status list is configuration that the Crawl4AI path doesn't act on; it
documents intent and is available to the shared HTTP layer.

---

### 2.6 Content fingerprinting — `src/aeo/crawl/fingerprint.py`

**Design intent (docstring, `fingerprint.py:1`).** Content-hash-based
skip-if-unchanged — avoid re-running extraction + scoring when a page hasn't
changed since the last crawl.

- **`should_skip(url_normalized, new_hash) -> bool`** (`fingerprint.py:9`).
  Returns `True` (skip) only when **all** hold:
  1. `cfg.fingerprint.enabled` is `True` (config default `enabled: true`,
     `algorithm: sha256`).
  2. `new_hash` is truthy (no hash → never skip; e.g. an empty-HTML fetch).
  3. The stored previous hash exists and equals `new_hash`.

  **DB read:** `pages_repo.last_hash(url_normalized)` (`storage/repos/pages.py:66`)
  runs `SELECT content_hash FROM crawled_pages WHERE url_normalized = %s AND
  content_hash IS NOT NULL ORDER BY crawled_at DESC LIMIT 1` — the most recent
  non-null hash for that URL across any run.

The `new_hash` it compares is exactly the `content_hash` that `_to_fetched`
stamped on the `FetchedPage` (SHA-256 of whitespace-collapsed HTML), so
whitespace-only edits don't defeat the short-circuit and trigger needless
re-optimization.

---

### 2.7 Page prioritization — `src/aeo/crawl/prioritize.py`

**Design intent (docstring, `prioritize.py:1-17`).** A crawl surfaces far more
pages than the per-page pipeline should process, so prioritization ranks them:

```
final_score = base_weight(page_type) × traffic_signal
```

`base_weight` reflects how much a page-type is worth optimizing for AEO (from
config); `traffic_signal` is the internal-link count today (a GSC clicks export
can replace it later). Rank descending, flag the top-N as `selected`, persist the
**full** ranking for observability. Pure functions over an explicit
`PrioritizationCfg`, so it's testable and tunable from `config/prioritization.yaml`
with no code change.

#### Defaults / magic numbers (`prioritize.py:27-31`)

- `DEFAULT_PRECEDENCE = ("utility", "blog", "pillar", "solution", "product",
  "contact", "about")` — first-match-wins order (see below).
- `DEFAULT_TOP_N = 30`.
- `DEFAULT_BASE_WEIGHT = 0.5`.

#### `PrioritizationCfg` (`prioritize.py:33`)

Fields: `base_weights: dict[str,float]`, `url_patterns: dict[str,list[str]]`,
`precedence: tuple[str,...]`, `homepage_paths: list[str]`, `top_n: int (30)`,
`min_traffic_signal: float (1.0)`, `default_type: str ("default")`.
`base_weight_for(page_type)` returns the type's weight, else the `default_type`
weight, else `0.5`.

#### `PageInput(url, internal_links=0)` and `ScoredUrl(...)` (`prioritize.py:49,57`)

`ScoredUrl` carries `url`, `page_type`, `base_weight`, `traffic_signal`,
`final_score`, `rank` (1-based), `selected` (bool).

#### `classify(url, cfg) -> str` (`prioritize.py:72`)

Page-type from the URL **path** (lowercased, trailing slash stripped):
1. If the path is `/` or in `cfg.homepage_paths` → `"homepage"`.
2. Else walk `cfg.precedence`; for each type, if **any** of its `url_patterns`
   substrings appears in the path → that type. **First match wins.**
3. Else `cfg.default_type` (`"default"`).

Precedence matters because patterns are substring matches against the full path:
`utility` is checked before `blog`/`product` so a `/account` page isn't
mis-typed, and `blog` before `product` so `/blog/new-product` classifies as
`blog`, not `product` (this exact case is called out in the YAML comment,
`prioritization.yaml:36-38`). Singular needles also catch plurals
(`/product` matches `/products`).

#### `rank(pages, cfg) -> list[ScoredUrl]` (`prioritize.py:88`)

For each page: `classify`, look up `base_weight`, compute `traffic_signal =
max(min_traffic_signal, float(internal_links))` (the floor stops `×0` from
zeroing out pages with no inbound links), and `final_score =
round(base_weight × traffic_signal, 3)`. Sort by `(-final_score, -base_weight,
url)` — score desc, then base_weight desc, then URL asc — so **ties break
deterministically and runs are reproducible**. Assign `rank` (1-based) and set
`selected = rank <= top_n`.

#### Config loading & convenience

- **`load_prioritization_cfg()`** (`prioritize.py:113`, `@lru_cache(maxsize=1)`).
  Reads `prioritization.yaml` via `load_yaml_file`, coercing types (float weights,
  lowercased pattern needles, lowercased trailing-slash-stripped homepage paths).
  Cached so the YAML is parsed once per process.
- **`prioritize(pages, cfg=None)`** (`prioritize.py:135`). Convenience wrapper:
  `rank(pages, cfg or load_prioritization_cfg())`.

#### `persist_ranking(run_id, scored)` (`prioritize.py:140`)

**Side effect — DB writes.** Upserts every `ScoredUrl` (not just the selected
ones) into the `page_priorities` table via `priorities_repo.upsert(run_id, url,
page_type, base_weight, traffic_signal, final_score, final_rank=rank,
selected=selected)`. Persisting the full ranking (with the `selected` flag) gives
observability into *why* a page was or wasn't picked.

#### Actual weights & vocabulary (`config/prioritization.yaml`)

`top_n: 30`, `min_traffic_signal: 1.0`, `default_type: default`.

| page_type | base_weight | rationale (YAML comment) |
|---|---|---|
| pillar | 1.0 | cornerstone informational content ranks highest |
| product | 0.9 | commercial pages |
| solution | 0.9 | commercial pages |
| blog | 0.8 | |
| homepage | 0.7 | |
| about | 0.4 | |
| contact | 0.3 | |
| utility | 0.2 | thin utility pages rank lowest |
| default | 0.5 | fallback when no pattern matches |

`homepage_paths: [/home, /index.html, /index.htm]` (plus `/` always).
`precedence: [utility, blog, pillar, solution, product, contact, about]`.

`url_patterns` (substring-against-path vocabulary):
- **utility:** `/login, /signin, /sign-in, /register, /privacy, /terms, /legal,
  /sitemap, /cookie, /account`
- **blog:** `/blog, /article, /news, /post`
- **pillar:** `/resource, /guide, /learn, /glossary, /what-is, /define,
  /research, /report, /whitepaper, /knowledge`
- **solution:** `/solution, /use-case`
- **product:** `/product, /platform`
- **contact:** `/contact, /demo, /request, /get-started, /free-trial, /trial,
  /pricing`
- **about:** `/about, /company, /team, /career, /leadership, /partner`

**Worked example.** A recursively discovered pillar page (`/guide/...`) with 12
inbound links scores `1.0 × max(1.0, 12) = 12.0`. A sitemap-only contact page
(no graph, so `internal_links=0`) scores `0.3 × max(1.0, 0) = 0.3`. The pillar
page is selected long before the contact page.

---

### 2.8 How discovery, prioritization and the inbound-link graph connect

1. `discover(domain)` returns a `DiscoveryResult` of up to `max_urls` (200)
   `DiscoveredUrl`s. Sitemap URLs have `internal_links=0`; recursive URLs carry
   real inbound counts.
2. Those map to `PageInput(url, internal_links)`.
3. `prioritize(...)` classifies + scores them; `traffic_signal` = the inbound
   count (floored at 1.0).
4. The top `top_n` (30) are `selected`; `persist_ranking` writes the lot to
   `page_priorities`.
5. Only the selected URLs are handed to `fetch_many(...)` for the expensive
   Crawl4AI render — `discovery.max_urls` caps the inventory, `prioritization.top_n`
   caps what actually gets rendered/optimized.

So the two YAML files are a two-stage funnel: `crawler.yaml → discovery.max_urls`
(how wide to look) then `prioritization.yaml → top_n` (how many to act on).

---

### 2.9 Force-IPv4 transport seam — `src/aeo/crawl/transport.py`

**Design intent (docstring, `transport.py:1-11`).** On **OCI Ampere (ARM)** the
default dual-stack resolver can silently stall scraper fetches on AAAA (IPv6)
records. Binding the `httpx` client to the IPv4 local address `0.0.0.0` forces
`AF_INET`, so every outbound call uses IPv4. Off by default (dev and most clouds
are fine); flip `AEO__CRAWLER__FORCE_IPV4=true` on OCI. This is the **one seam**
every network client (discovery, PageSpeed, Perplexity, the LLM cloud backend) is
meant to route through, so the policy lives in exactly one place.

- `_IPV4_LOCAL_ADDRESS = "0.0.0.0"` (`transport.py:19`).
- **`force_ipv4_enabled() -> bool`** (`transport.py:22`) — reads
  `get_settings().crawler.force_ipv4` (default `False`, `settings.py:74`).
- **`sync_transport() -> httpx.HTTPTransport | None`** (`transport.py:26`) —
  returns `httpx.HTTPTransport(local_address="0.0.0.0")` when forced, else `None`
  (so an `httpx.Client` falls back to its default transport).
- **`async_transport() -> httpx.AsyncHTTPTransport | None`** (`transport.py:34`) —
  the async equivalent. This is what `discovery._default_fetch_text` passes as
  `transport=async_transport()` (`discovery.py:150`).

**Config knob.** `crawler.force_ipv4: bool = False` (`settings.py:74`), settable
via `AEO__CRAWLER__FORCE_IPV4`. (It's not present in `crawler.yaml`'s defaults, so
it relies on the Pydantic default unless set by env var.) Returning `None` rather
than always building an IPv4 transport keeps non-OCI environments on httpx's
default behavior with zero overhead.

---

### 2.10 Settings reference (section `crawler`, `config/crawler.yaml` ↔ `settings.py`)

All overridable via `AEO__CRAWLER__*` (nested with `__`, e.g.
`AEO__CRAWLER__RATE_LIMIT__BURST`).

| Key | Default | Consumed by |
|---|---|---|
| `user_agent` | `AEOBot/0.2 (+https://securin.io/bot)` | discovery fetch, robots check |
| `concurrency` | `4` | `fetch_many` semaphore |
| `request_timeout_sec` | `30` | `CrawlClient._fetch_once` `wait_for` |
| `respect_robots` | `true` | `RobotsCache.allowed` |
| `force_ipv4` | `false` | `transport.py` (§2.9) |
| `rate_limit.requests_per_minute` | `30` | `RateLimiter` refill (0.5/s) |
| `rate_limit.burst` | `5` | `RateLimiter` bucket capacity |
| `retry.max_attempts` | `4` | `fetch_retry` stop |
| `retry.initial_backoff_sec` | `1.5` | backoff multiplier |
| `retry.max_backoff_sec` | `30` | backoff cap |
| `retry.retry_on_status` | `[408,425,429,500,502,503,504]` | declared, not used by Crawl4AI path (§2.5) |
| `fingerprint.enabled` | `true` | `should_skip` |
| `fingerprint.algorithm` | `sha256` | `content_hash` |
| `browser.headless` | `true` | `_AsyncWebCrawler` |
| `browser.remove_overlay_elements` | `true` | `CrawlerRunConfig` |
| `browser.word_count_threshold` | `0` | `CrawlerRunConfig` |
| `discovery.max_urls` | `200` | sitemap + recursive caps; frontier/inbound cap base |
| `discovery.max_depth` | `2` | recursive BFS depth |
| `discovery.max_sitemaps` | `50` | sitemap-index expansion cap |
| `discovery.timeout_sec` | `15` | discovery `httpx` timeout |

## 3. Extractors (Part A) — Registry, Meta, Schema, Q&A, Stats, Entities, E-E-A-T

This section documents the first half of the deterministic extractor layer. Extractors are the bridge between raw fetched HTML and the scoring engine: each one pulls a narrow, well-defined set of signals off the page and returns a plain `dict` that gets stored in an `ExtractionBundle` under the extractor's registered name. The scorers (documented elsewhere) read those dicts back by name. Everything here is deterministic — no LLM calls, no network — which is the whole design intent: the cheap, reproducible signal extraction runs first, and the expensive/uncertain LLM judgments layer on top.

### 3.0 The extractor contract (`extract/base.py`, `extract/__init__.py`)

#### The `Extractor` protocol — `base.py:10`

Every extractor is a *pure function* with one fixed signature, codified as a `typing.Protocol`:

```python
class Extractor(Protocol):
    def __call__(self, html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]: ...
```

So the contract is: **`extract(html, soup, url) -> dict`**. The three inputs are:

| Param | Type | Why both `html` and `soup`? |
|-------|------|------------------------------|
| `html` | `str` | The raw HTML string. Most extractors here ignore it and use `soup`, but it is passed so an extractor *can* run a raw-string regex (e.g. render-mode comparisons) without re-serializing the tree. |
| `soup` | `BeautifulSoup` | A **freshly parsed** tree, one per extractor (see below). |
| `url` | `str` | The page URL, used by `entities.py` to pick the primary entity by host. |

The return value is always a flat-ish `dict[str, Any]`; there is no shared base class for results — the "interface" is just the dict keys each scorer expects.

#### Why a fresh `BeautifulSoup` per extractor (the destructive-text gotcha)

The runner that drives these extractors is `ExtractStage` in `src/aeo/pipeline/stages.py:26`. Its loop builds a **new** soup for *every* extractor (`stages.py:40-41`):

```python
for name, fn in self._extractors:
    soup = parse(page.html)        # fresh tree per extractor
    try:
        bundle.put(name, fn(page.html, soup, page.url))
    except Exception as exc:       # one bad extractor shouldn't kill the page
        log.warning("extractor_failed", extractor=name, url=page.url, error=str(exc))
        bundle.put(name, {"error": str(exc)})
```

The reason (documented verbatim at `stages.py:29-33`) is that the shared text helper `utils.html.body_text()` is **destructive**: it calls `tag.decompose()` to physically delete chrome nodes from the tree (`utils/html.py:21-25`):

```python
CHROME_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "iframe")

def body_text(soup):
    for tag in soup(CHROME_TAGS):
        tag.decompose()           # mutates the tree in place
    return soup.get_text(separator=" ", strip=True)
```

If extractors shared one soup, then once `stats` or `entities` called `body_text()` and ripped out `<script>` / `<footer>` / etc., a later extractor like `schema_jsonld` (which reads `<script type="application/ld+json">`) or `links` (which reads `<a>` in nav/footer) would silently see an empty or mutilated tree. A fresh parse per extractor makes each one independent and order-insensitive. The trade-off is re-parsing the HTML N times (12 extractors), which is accepted as cheap relative to fetching/scoring.

Note the error isolation in the same loop: a raising extractor is caught, logged with `extractor_failed`, and stored as `{"error": str(exc)}` so one bad extractor never kills the whole page. PageSpeed data is *not* an extractor — it is fetched externally (async) and merged into the bundle after the loop at `stages.py:48-49`.

#### The `DEFAULT_EXTRACTORS` registry — `extract/__init__.py:20-33`

`__init__.py` imports all twelve extractor modules and declares the ordered registry the pipeline iterates. Order is preserved (it is a `list` of `(name, fn)` tuples), though because each extractor gets its own soup, **order does not affect correctness** — it only affects the order keys appear in the bundle / logs.

| # | Registered name | Function | Covered in |
|---|-----------------|----------|------------|
| 1 | `meta` | `meta.extract` | this section (§3.1) |
| 2 | `headings` | `headings.extract` | Part B |
| 3 | `schema_jsonld` | `schema_jsonld.extract` | §3.2 |
| 4 | `qa_blocks` | `qa_blocks.extract` | §3.3 |
| 5 | `stats` | `stats.extract` | §3.4 |
| 6 | `entities` | `entities.extract` | §3.5 |
| 7 | `eeat` | `eeat.extract` | §3.6 |
| 8 | `links` | `links.extract` | Part B |
| 9 | `readability` | `readability.extract` | Part B |
| 10 | `render_mode` | `render_mode.extract` | Part B |
| 11 | `glossary` | `glossary.extract` | Part B |
| 12 | `chunker` | `chunker.extract` | Part B |

The module docstring states the extension contract: "Adding a new extractor = add the module + append here" (`__init__.py:18-19`). `ExtractStage.__init__` accepts the registry as an injectable default (`stages.py:35`), so tests can substitute a smaller list.

---

### 3.1 `meta.py` — title, description, canonical, language, og:type

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`meta.py:10`)

The simplest extractor. It pulls five `<head>`-level metadata fields and returns them as strings (empty string when absent — never `None`).

What it reads (`meta.py:11-17`):

| Output key | Source tag / logic | Code |
|------------|--------------------|------|
| `title` | `<title>` text content (stripped) | `meta.py:11-12` |
| `description` | `<meta name="description">` content, falling back to `<meta property="og:description">` | `meta.py:14` |
| `canonical` | `<link rel="canonical">` `href` | `meta.py:15` |
| `lang` | `<html lang="...">` attribute | `meta.py:16` |
| `og_type` | `<meta property="og:type">` content | `meta.py:17` |

Two private helpers do the lookups:

- `_meta_content(soup, name)` (`meta.py:28-30`) — finds a `<meta>` by **either** `name=` **or** `property=` (so it transparently handles both standard meta tags and OpenGraph `property` tags), returns the stripped `content` or `""`.
- `_link_href(soup, rel)` (`meta.py:33-35`) — finds `<link rel=...>` and returns its stripped `href` or `""`.

**Returns** (`meta.py:19-25`):
```python
{"title": str, "description": str, "canonical": str, "lang": str, "og_type": str}
```

**Side effects:** none (pure read). **Config knobs:** none — selectors are hard-coded. The `description`→`og:description` and `name`→`property` fallbacks are the only "intelligence" here; the intent is to maximize the chance of capturing a description even on pages that only set OpenGraph tags.

---

### 3.2 `schema_jsonld.py` — JSON-LD structured-data catalog

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`schema_jsonld.py:16`)

This feeds **criterion 1 (Schema Markup)** and also surfaces author/date lists that `eeat.py` (and its scorer) can fall back to (per the module docstring, `schema_jsonld.py:1-6`).

**Algorithm** (`schema_jsonld.py:16-50`):
1. Iterate every `<script type="application/ld+json">` (`:23`).
2. Take the script's text (`script.string or script.text`), strip it; skip empties (`:24-26`).
3. `json.loads` it. On `JSONDecodeError`, increment an `invalid` counter and skip — invalid blocks are *counted*, not crashed on (`:27-31`). This is deliberate: malformed JSON-LD is itself a negative signal worth surfacing.
4. For each *object* yielded by `_iter_objects(parsed)`:
   - append the raw object to `blocks`,
   - read its `@type`. If `@type` is a **list**, extend the type list with every element; if it's a scalar truthy value, append it (`:34-38`). This handles multi-typed nodes like `"@type": ["Article", "BlogPosting"]`.
   - harvest authors via `_collect_authors` and dates via `_collect_dates`.

**`_iter_objects(node)`** (`schema_jsonld.py:53-61`) — a recursive generator that flattens the JSON-LD graph. It yields each `dict`, and crucially **descends into `@graph`** arrays (`:56-58`), and recurses through plain lists (`:59-61`). This is what lets the extractor see entities buried inside a top-level `{"@graph": [...]}` wrapper, which is how most CMS plugins (Yoast, RankMath) emit schema.

**`_collect_authors(obj, out)`** (`schema_jsonld.py:71-83`) — looks at keys `author` and `creator`. A value can be a `dict` (take `.name`), a `list` of dicts (take each `.name`), or a bare `str` (take as-is). Anything else is ignored.

**`_collect_dates(obj, out)`** (`schema_jsonld.py:86-90`) — collects the string values of keys `datePublished`, `dateModified`, and `uploadDate` (the last covers `VideoObject`).

**`_counts(items)`** (`schema_jsonld.py:64-68`) — a tiny frequency counter producing `{type: count}`.

**Returns** (`schema_jsonld.py:42-50`):
```python
{
  "block_count":    int,            # total JSON-LD objects found (across all <script>s, graph-flattened)
  "invalid_blocks": int,            # scripts that failed json.loads
  "types":          list[str],      # sorted, de-duplicated @type values
  "type_counts":    dict[str,int],  # frequency per @type
  "authors":        list[str],      # raw author/creator names (may repeat)
  "dates":          list[str],      # raw date strings
  "blocks":         list[dict],     # every parsed object, kept verbatim
}
```

**Side effects:** none. **Config knobs:** none in this module — but the *scorer* that consumes `types` checks them against `criteria.schema_markup.valued_types` in `config/scoring.yaml:15-16`: `FAQPage, HowTo, TechArticle, Article, NewsArticle, Organization, DefinedTerm, ItemList, BreadcrumbList, Product, BlogPosting`. So while this extractor catalogs *all* types neutrally, those eleven are the ones that earn schema credit downstream.

---

### 3.3 `qa_blocks.py` — real question→answer pair detection

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`qa_blocks.py:17`)

**Intent (module docstring, `qa_blocks.py:1-6`):** detect *genuine* Q&A structure — a question heading that is actually answered by adjacent prose — rather than just counting question marks. This drives the **Q&A Blocks** criterion.

**Config it consumes** — reads `criteria.qa_blocks` from `config/scoring.yaml` via `load_yaml_file` (`qa_blocks.py:18-20`):

```yaml
qa_blocks:
  label: "Q&A Blocks"
  weight: 1.0
  min_answer_chars: 80
  question_words: [what, why, how, when, where, who, which, is, are, do, does, can, should]
```

- `min_answer_chars` — default `80` (the code's literal fallback is also `80`, `qa_blocks.py:19`). A following paragraph must be **≥ 80 characters** to count as a real answer.
- `question_words` — the 13 interrogative/auxiliary words above, lowercased into a set (`qa_blocks.py:20`). A heading whose **first word** is one of these counts as a question even without a trailing `?`.

**Algorithm** (`qa_blocks.py:22-50`):
1. Walk every `<h2>`, `<h3>`, `<h4>` (`:25`). H1 is excluded — page titles aren't questions.
2. For each heading text: compute `ends_q` (ends with `?`) and `starts_q` (first word ∈ `question_words`) (`:29-31`). If neither, skip (`:33-34`).
3. Every heading that passes increments `question_heading_count` (`:36`) — this is the *denominator-style* signal (how many question-shaped headings exist, answered or not).
4. Grab the answer text via `_next_paragraph(heading)`. If it exists **and** is ≥ `min_answer` chars, record a pair (`:37-44`).

**`_next_paragraph(node)`** (`qa_blocks.py:53-69`) — the "is it actually answered" check. Starting from the heading, it walks **up to the next 3 sibling elements** (`hops < 3`, `:58`), concatenating their text, and stops early if:
- it hits another **heading** — specifically a 2-char `h?` where the second char is a digit, i.e. `h1`–`h9` (`:60-61`), so the answer can't bleed past the next section; or
- the accumulated text reaches **≥ 200 chars** (`:65-66`), an early-exit cap so it doesn't slurp an entire long section.

So a "pair" requires: a question-shaped H2/H3/H4 immediately followed (within 2–3 siblings, before the next heading) by ≥ 80 chars of prose.

**Returns** (`qa_blocks.py:46-50`):
```python
{
  "qa_pairs": [
     {"question": str,
      "answer_preview": str,   # answer truncated to 280 chars
      "answer_chars": int,     # full length
      "heading_tag": str},     # "h2" | "h3" | "h4"
     ...
  ],
  "pair_count": int,                 # number of fully-qualified pairs
  "question_heading_count": int,     # question-shaped headings (answered or not)
}
```

**Side effects:** none. **Magic numbers:** `min_answer_chars=80` (config), 3-sibling hop window and 200-char accumulation cap (both hard-coded in `_next_paragraph`), 280-char `answer_preview` truncation (`:41`).

---

### 3.4 `stats.py` — concrete numeric-claim extraction

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`stats.py:19`)

**Intent (docstring, `stats.py:1-5`):** find "concrete" statistical claims (percentages, money, CVEs, etc.) in body prose. Strategy is **over-collect with regex, then prune** false positives and dedupe — accepting recall-first then filtering, because a missed stat hurts the score but a few junk hits are cheaply blacklisted.

**Config it consumes** — `stats` block from `config/extractors.yaml` (`stats.py:20`). The actual patterns (`extractors.yaml:7-15`):

| Pattern name | Regex | Matches |
|--------------|-------|---------|
| `percent` | `\b\d{1,3}(?:\.\d+)?\s?%` | e.g. `42%`, `3.5 %` |
| `multiplier` | `\b\d+(?:\.\d+)?x\b` | e.g. `3x`, `2.5x` |
| `money` | `(?i)(?:\$\|usd\|eur\|£\|€)\s?\d[\d,.]*(?:\s?(?:million\|billion\|k\|m\|b))?` | e.g. `$1,200`, `€3 million` |
| `big_num` | `\b\d{1,3}(?:,\d{3})+\b` | comma-grouped numbers, e.g. `1,234,567` |
| `cve` | `CVE-\d{4}-\d{4,7}` | CVE IDs, e.g. `CVE-2021-44228` |
| `cwe` | `CWE-\d+` | CWE IDs |
| `cvss` | `(?i)\bcvss(?:v?\d)?\s*[:=]?\s*\d+(?:\.\d+)?\b` | e.g. `CVSS 9.8`, `cvssv3:7.5` |
| `time_unit` | `(?i)\b\d+(?:\.\d+)?\s*(?:seconds\|minutes\|hours\|days\|weeks\|months\|years)\b` | e.g. `30 days`, `2.5 hours` |

The domain skew is deliberate — this is a cybersecurity audit tool, so CVE/CWE/CVSS identifiers are treated as first-class "statistics" alongside generic percent/money/count claims.

**Blacklist** (`extractors.yaml:18-21`) — a hit is discarded if it matches **any** of these (`stats.py:33`):

| Blacklist regex | Kills |
|-----------------|-------|
| `(?i)page\s+\d+` | "Page 4" pagination references |
| `(?i)fig(?:ure)?\.\s*\d+` | "Fig. 2" / "Figure 3" captions |
| `\b(?:19\|20)\d{2}\b\s*(?:©\|all rights reserved)` | copyright-year boilerplate |

**Algorithm** (`stats.py:19-49`):
1. Compile each pattern and each blacklist regex (`:21-24`).
2. Get prose via `body_text(soup)` (`:26`) — **body only**, chrome stripped, so footer "© 2024" and nav numbers don't pollute (this is the destructive call that mandates the fresh-soup design).
3. For each pattern, `finditer` over the text (`:30-31`). For each match: strip it, skip if empty or if any blacklist regex matches it (`:32-33`).
4. **Dedupe** by a normalized key `f"{kind}:{value.lower()}"` (`:35-38`) — the same value found by the same pattern is recorded once, even if it appears many times.
5. Record `{"kind", "value", "context"}` where `context` is the surrounding text (`:39`).
6. Tally `by_kind` counts (`:41-43`).

**`_context(text, m, window=60)`** (`stats.py:52-55`) — returns up to 60 chars on each side of the match, clamped to string bounds and stripped. Gives reviewers a snippet to judge whether the stat is meaningful.

**Returns** (`stats.py:45-49`):
```python
{
  "hits":    [{"kind": str, "value": str, "context": str}, ...],
  "count":   int,                 # distinct hits after dedupe + blacklist
  "by_kind": {kind: int, ...},
}
```

**Side effects:** none (but note it consumes a destructive `body_text` on its private soup). **Downstream:** the `stats_in_html` scorer maps `count` to a tier via `config/scoring.yaml:30` `tiers: {1:0, 2:1, 3:3, 4:6, 5:10}` — i.e. you need **≥10 distinct concrete claims** for tier 5, 6 for tier 4, etc.

---

### 3.5 `entities.py` — brand vs. first-person mention ratio

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`entities.py:20`)

**Intent (docstring, `entities.py:1-6`):** drive **criterion 4 (Entity Consistency)** — does the page consistently name the brand entity, or does it hide behind "we/our/us"? Counts come from **body text only** (chrome stripped) so footer brand mentions don't inflate the ratio.

**Config it consumes** — the `entities` map from `config/entities.yaml` (`entities.py:21`). If the config is empty it short-circuits to a zeroed result (`entities.py:22-23`). The full vocabulary (`entities.yaml:5-58`):

| Key (entity) | Canonical | Aliases | Domain | first_person |
|--------------|-----------|---------|--------|--------------|
| `Securin` | Securin | `Securin Inc`, `Securin.io` | securin.io | we, our, us, ours, ourselves |
| `Pentera` | Pentera | `Pentera Labs` | pentera.io | we, our, us, ours, ourselves |
| `Cymulate` | Cymulate | — | cymulate.com | we, our, us |
| `XM Cyber` | XM Cyber | `XMCyber` | xmcyber.com | we, our, us |
| `AttackIQ` | AttackIQ | `Attack IQ` | attackiq.com | we, our, us |
| `Picus Security` | Picus Security | `Picus` | picussecurity.com | we, our, us |
| `Hive Pro` | Hive Pro | `HivePro` | hivepro.com | we, our, us |
| `Ridge Security` | Ridge Security | `Ridge` | ridgesecurity.ai | we, our, us |
| `SecureLayer7` | SecureLayer7 | `Secure Layer 7`, `SL7` | securelayer7.net | we, our, us |

These are Securin and its competitor set — the tool is built to audit how these specific vendors present their entities. Matching is **case-insensitive, whole-word** (per the file's own header comment, `entities.yaml:1-3`).

**Algorithm** (`entities.py:20-58`):
1. `body_text(soup)` for the prose; `host_of(url)` for the page's hostname (`:25-26`).
2. **Pick the primary entity** by host: the first config entity whose `domain` is a suffix of the page host (`host.endswith(ent["domain"])`) becomes `primary_key` (`:29-33`). So on `blog.securin.io`, Securin is primary.
3. For **every** configured entity, count occurrences of its canonical name + all aliases via `_count_word`, summed (`:36-40`). Each becomes a `detected` entry `{name, count, is_primary}`.
4. `primary` is the detected entry flagged `is_primary` (`:42`).
5. **First-person count:** only for the primary entity — sum `_count_word` over its `first_person` markers (`:44-47`). (No primary → 0.)
6. `entity_count` = the primary's mention count (0 if no primary) (`:49`).
7. `ratio` = `entity_count / first_person_count`, or **`None`** when there are zero first-person mentions (avoids divide-by-zero; signals "not applicable") (`:50`).

**`_count_word(text, term)`** (`entities.py:61-66`) — compiles `\b{escaped term}\b` case-insensitively and returns the match count. `re.escape` makes multi-word terms like "XM Cyber" or "Secure Layer 7" match literally as a whole phrase. Empty term → 0.

**Returns** (`entities.py:52-58`):
```python
{
  "detected": [{"name": str, "count": int, "is_primary": bool}, ...],   # all configured entities
  "primary":  {"name", "count", "is_primary"} | None,
  "entity_count":       int,        # primary entity mentions
  "first_person_count": int,        # we/our/us... for the primary
  "ratio":  float | None,           # entity_count / first_person_count
}
```

**Side effects:** none. **Downstream:** the `entity_consistency` scorer maps `ratio` to tiers via `config/scoring.yaml:37` `tiers: {1:0.0, 2:0.5, 3:1.0, 4:1.5, 5:2.5}` — interpreted (per the YAML comment) as `1.0` = balanced, `>1.0` = entity-dominant (good), `<1.0` = first-person dominant (bad). So a page that says "Securin" 2.5× as often as "we/our/us" hits tier 5.

---

### 3.6 `eeat.py` — author, publish date, credentials (E-E-A-T)

**Signature:** `extract(html, soup, url) -> dict[str, Any]` (`eeat.py:18`)

**Intent (docstring, `eeat.py:1-5`):** extract the Experience/Expertise/Authoritativeness/Trust signals — *who* wrote it, *when*, and whether recognized *credentials* are present. It falls through multiple selector strategies (meta tags → CSS classes → microdata) before giving up.

**Config it consumes** — two files:
- `config/extractors.yaml` for `author_selectors` and `date_selectors` (`eeat.py:19, 22-23`).
- `config/scoring.yaml` → `criteria.citation_signals.credentials` for the credential vocabulary (`eeat.py:20, 24`).

**Author selectors** (`extractors.yaml:27-40`) — tried in order, **first hit wins** (`eeat.py:35-46`):
- `meta` (read the `content` attr): `meta[name="author"]`, `meta[property="article:author"]`, `meta[name="dc.creator"]`.
- `css` (read text): `.author`, `.byline`, `[rel=author]`, `[itemprop=author]`.
- (`jsonld_paths` `author.name` / `creator.name` are listed in the YAML but **not read by this module** — the JSON-LD author fallback lives in `schema_jsonld.py`'s `authors` output, which the scorer can consult.)

**Date selectors** (`extractors.yaml:42-52`) — same meta-then-css order (`eeat.py:49-60`):
- `meta`: `meta[property="article:published_time"]`, `meta[property="article:modified_time"]`, `meta[name="date"]`, `meta[name="dc.date"]`.
- `css`: `time[datetime]`, `.published`, `.publish-date`, `[itemprop=datePublished]`. For CSS hits it prefers the element's `datetime` **attribute**, falling back to its text (`eeat.py:57`).

**Credentials vocabulary** (`scoring.yaml:75-76`) — 15 security certifications:
`CISSP, OSCP, OSCE, CEH, GIAC, GPEN, GWAPT, GCIH, GCFA, CISA, CISM, CRISC, CCSP, OSWE, OSED`.

**Algorithm:**
- `_find_author(soup, sel)` (`eeat.py:35-46`) — iterate meta selectors first (return `content` if present), then CSS selectors (return stripped text), else `None`.
- `_find_date(soup, sel)` (`eeat.py:49-60`) — same pattern, with the `datetime`-attr preference noted above.
- `_find_credentials(soup, credentials)` (`eeat.py:63-72`) — if the vocab is empty, return `[]`. Otherwise take **all** page text (`soup.get_text` — note: *not* `body_text`, so credentials in author bios anywhere on the page count) and for each credential test a whole-word, case-insensitive regex `\b{cred}\b`. Returns the **sorted, de-duplicated** set of credentials found.

**Returns** (`eeat.py:26-32`):
```python
{
  "author":               str | None,
  "publish_date":         str | None,
  "credentials_mentioned": list[str],   # sorted, unique
  "has_author":           bool,         # bool(author)
  "has_date":             bool,         # bool(date)
}
```

**Side effects:** none. **Downstream:** the `citation_signals` scorer combines `has_author`, `has_date`, and authority-link counts against the tier table at `config/scoring.yaml:78-83`:

| Tier | author | date | authority_links |
|------|--------|------|-----------------|
| 1 | false | false | 0 |
| 2 | false | false | 1 |
| 3 | false | true | 2 |
| 4 | true | true | 3 |
| 5 | true | true | 5 |

So reaching tier 4+ requires a detected author **and** date **and** ≥3 external authority links (those links come from the separate `links` extractor in Part B, checked against `citation_signals.authority_domains` — `nist.gov`, `cisa.gov`, `nvd.nist.gov`, `mitre.org`, `owasp.org`, `ietf.org`, `first.org`, `sans.org`, `verizon.com`, `microsoft.com`, `google.com`, `cloudflare.com` at `scoring.yaml:61-73`). Credentials are an additional E-E-A-T trust signal surfaced for the scorer/report.

---

### 3.7 Cross-cutting notes

- **Purity & determinism:** none of these seven extractors make network calls, write files, or touch the DB. Their *outputs* are persisted by `ExtractStage` into an `ExtractionBundle` (which the storage layer writes via `extractions_repo`), but the extractors themselves are side-effect-free. This is the "deterministic-first" seam: cheap reproducible signals first, LLM judgments later.
- **Config loading:** `qa_blocks`, `stats`, `entities`, and `eeat` all pull thresholds/vocabularies from YAML via `..settings.load_yaml_file` (`scoring.yaml`, `extractors.yaml`, `entities.yaml`). `meta` and `schema_jsonld` are config-free. This means the question vocabulary, stat regexes, entity list, and credential set can all be tuned without code changes.
- **`body_text` users vs. full-tree users:** `stats` and `entities` use the destructive body-only text (chrome stripped, so footer/nav noise is excluded); `eeat`'s credential scan uses the full `soup.get_text` (credentials may live in author bios anywhere); `schema_jsonld` reads raw `<script>` nodes that `body_text` would have decomposed. This divergence is precisely why each extractor must get its own fresh soup.

## 4. Extractors (Part B) — Headings, Readability, Chunker, Links, Render-Mode, Glossary, PageSpeed

This section documents the second half of the deterministic extractor suite. Each module lives under `src/aeo/extract/` and exposes a single public `extract(...)` entry point that takes the page HTML and a parsed `BeautifulSoup` tree and returns a plain `dict` of features. These dicts are consumed by the scorers (criteria 5, 7, 8, content-depth, QA-blocks) and by downstream RAG/embedding. The extractors are intentionally **deterministic and side-effect-free** with one exception: the PageSpeed client makes a network call. None of these modules write to the database or to disk.

### Shared contract and conventions

Every extractor here follows the same signature shape:

```python
def extract(html: str, soup: BeautifulSoup, url: str, ...) -> dict[str, Any]:
```

- `html` — the raw HTML string (the *initial* server response, before any JS render mutation).
- `soup` — a `BeautifulSoup` tree. Note: `body_text(soup)` is **destructive** (`src/aeo/utils/html.py:21`) — it `decompose()`s chrome tags (`script, style, nav, footer, header, aside, noscript, iframe`, see `utils/html.py:8`). Extractors that call `body_text` mutate the shared soup, so ordering across extractors matters; this is why `headings`/`chunker`/`glossary`/`links` (which read structural tags) generally run before or independently of `readability`/`render_mode` (which strip chrome).
- Config is loaded lazily inside `extract()` via `load_yaml_file("extractors.yaml")` / `load_yaml_file("scoring.yaml")` (`src/aeo/settings.py:243`), which is process-cached, so re-reading per page is cheap.

The shared helpers these modules rely on (`src/aeo/utils/`):

| Helper | Location | Behavior |
|---|---|---|
| `body_text(soup)` | `utils/html.py:21` | Strips chrome tags then `get_text(separator=" ", strip=True)`. Destructive. |
| `word_count(text)` | `utils/text.py:16` | Counts `\b[\w\-]+\b` tokens (hyphenated words count as one). |
| `sentence_split(text)` | `utils/text.py:20` | Splits on `(?<=[.!?])\s+(?=[A-Z0-9])` after collapsing whitespace. |
| `absolute(base, href)` | `utils/url.py:29` | `urljoin` wrapper. |
| `host_of(url)` | `utils/url.py:21` | Lowercased hostname. |
| `same_site(a, b)` | `utils/url.py:25` | Compares cheap eTLD+1 (`_registrable` = last two labels, `utils/url.py:37`). |

---

### 4.1 `extract/headings.py` — Heading hierarchy + question detection

Feeds **criterion 5 (heading_structure)** and **QA-blocks** scoring (a question heading paired with its adjacent paragraph forms a Q→A block). The module docstring (`headings.py:1-7`) states both consumers explicitly.

**`extract(html, soup, url) -> dict[str, Any]`** (`headings.py:21`)

Algorithm:
1. Loads `template_h1_patterns` from `extractors.yaml` and compiles each to a regex (`headings.py:22-23`).
2. Initializes a `headings` dict with empty lists for `h1`..`h6` (`headings.py:25`).
3. Walks **every** heading element `h1`–`h6` in document order via `soup.find_all([...])` (`headings.py:28`). For each non-empty heading it:
   - extracts text with `el.get_text(separator=" ", strip=True)`;
   - appends to the per-level list;
   - appends to a flat `sequence` list, recording `tag`, `text`, and `is_question` (`headings.py:34-38`).
4. **Template-H1 detection** (`headings.py:40-41`): `template_h1` is `True` if *any* H1 on the page matches *any* configured template pattern (`p.search(h)`).
5. **Question-phrased H2/H3** (`headings.py:43-44`): concatenates the H2 and H3 lists, counts how many end in a question mark.

**Question detection** uses `_QUESTION_END_RE = re.compile(r".+\?\s*$")` (`headings.py:18`) — the heading must have at least one character followed by `?` and optional trailing whitespace. This is what makes a heading "question-phrased," the key AEO signal (AI answer engines prefer pages structured as explicit Q&A).

**`_hierarchy_ok(seq) -> bool`** (`headings.py:61`) — returns `False` if the document skips a level *downward* (e.g. `h2 → h4` with no intervening `h3`). It tracks the last seen level and fails when `level > last + 1`. Going back up any number of levels (e.g. `h4 → h2`) is allowed. Empty sequence → `True`.

**Output keys** (`headings.py:46-58`):

| Key | Meaning |
|---|---|
| `by_level` | `{h1: [...], ..., h6: [...]}` lists of heading text. |
| `sequence` | Ordered list of `{tag, text, is_question}`. |
| `counts` | `{h1: n, ..., h6: n}` per-level counts. |
| `h1_text` | First H1's text, or `None`. |
| `h1_count` | Number of H1s. |
| `missing_h1` | `True` when there are zero H1s. |
| `template_h1` | `True` if any H1 matches a template pattern. |
| `h23_total` | Combined H2+H3 count. |
| `h23_question_count` | How many H2/H3 are questions. |
| `h23_question_ratio` | `question_count / h23_total`, or `0.0` if no H2/H3. |
| `hierarchy_ok` | No downward level skips. |

**Config consumed — `extractors.yaml: template_h1_patterns`** (`config/extractors.yaml:60-65`). These are CMS template bugs the audit calls out — when a generic template title leaks through as the page H1:

```yaml
template_h1_patterns:
  - '^Resources$'
  - '^Welcome$'
  - '^Home$'
  - '^Page Not Found$'
```

**Side effects:** none beyond reading the cached YAML. Does not mutate soup (uses `find_all` + `get_text`, no decompose).

---

### 4.2 `extract/readability.py` — Flesch reading ease + sentence stats

Feeds the **content-depth scorer** (`readability.py:1`).

**`extract(html, soup, url) -> dict[str, Any]`** (`readability.py:14`)

Algorithm:
1. `text = body_text(soup)` — **destructive** chrome strip (`readability.py:15`).
2. `wc = word_count(text)`, `sentences = sentence_split(text)`, `sent_count = len(sentences)`.
3. **Short-page guard** (`readability.py:20-27`): if `wc < 50`, the readability scores are statistically meaningless, so it returns word/sentence counts but sets `flesch_reading_ease`, `flesch_kincaid_grade`, and `avg_sentence_length` all to `None`. The `50`-word floor is the design threshold for "enough text to score."
4. Otherwise it computes via the `textstat` library and rounds to 2 dp (`readability.py:29-35`):
   - `flesch_reading_ease(text)` — 0–100, higher = easier. ~60–70 ≈ plain English; the scorer rewards readable prose.
   - `flesch_kincaid_grade(text)` — US grade level.
   - `avg_sentence_length` = `wc / max(sent_count, 1)` (guard avoids div-by-zero).

**Output keys:** `word_count`, `sentence_count`, `flesch_reading_ease`, `flesch_kincaid_grade`, `avg_sentence_length`.

**Inputs → outputs:** soup → numeric stats dict. **Side effects:** mutates the soup (decomposes chrome). No network, no DB. **Config:** none (no YAML knobs; the `< 50` floor is a hardcoded constant).

---

### 4.3 `extract/chunker.py` — Passage-level chunking for RAG/embedding

Produces heading-anchored passages for downstream embedding / retrieval (`chunker.py:1-6`).

**`extract(html, soup, url, target_chars: int = 1200) -> dict[str, Any]`** (`chunker.py:15`)

Strategy (module docstring): walk the document, group paragraphs under their nearest preceding heading, flushing a chunk once it reaches the target size in **characters**. `target_chars` defaults to **1200** — the magic number is the approximate passage size tuned for embedding context windows.

Algorithm:
1. Maintains three pieces of running state: `current_heading`, `current_buf` (list of paragraph/list-item texts), `current_len` (running char count).
2. Inner closure **`flush()`** (`chunker.py:21-32`): if the buffer is non-empty, emits a chunk dict `{heading, text (joined+stripped), chars, index}` where `index` is the chunk's position, then resets the buffer and length.
3. Iterates over `h1, h2, h3, h4, p, li` in document order (`chunker.py:34`). The `isinstance(el, Tag)` guard (`chunker.py:35`) skips NavigableString nodes.
   - **If the element is a heading** (`el.name.startswith("h")`, `chunker.py:37`): `flush()` the current chunk, then set `current_heading` to this heading's text and continue. So each chunk is bounded by heading transitions.
   - **If it's a `p`/`li`** with non-empty text: append to buffer, add its length to `current_len`. If `current_len >= target_chars`, `flush()` mid-section (a long run of paragraphs under one heading splits into multiple chunks, all sharing the same `heading`).
4. Final `flush()` after the loop (`chunker.py:50`) emits the trailing chunk.

Note the chunker only considers `h1`–`h4` as boundaries (not `h5`/`h6`), and only `p`/`li` as body; other content (tables, blockquotes, code) is ignored.

**Output keys** (`chunker.py:51-55`): `chunks` (list), `chunk_count`, `total_chars` (sum of per-chunk `chars`).

**Inputs → outputs:** soup → list of passages. **Side effects:** none (uses `find_all`/`get_text`, no decompose). **Config:** none — `target_chars` is a call-time argument, default 1200.

---

### 4.4 `extract/links.py` — Internal / external / authority link analysis

External *authority* links feed **criterion 7 (E-E-A-T / citation_signals)** (`links.py:1-4`).

**`extract(html, soup, url) -> dict[str, Any]`** (`links.py:16`)

Algorithm:
1. Loads the authority allow-list by reaching into `scoring.yaml → criteria → citation_signals → authority_domains` (`links.py:17-22`). The list is shared with the scorer (single source of truth). Current values (`config/scoring.yaml:61-73`):

   | Authority domains |
   |---|
   | `nist.gov`, `cisa.gov`, `nvd.nist.gov`, `mitre.org`, `owasp.org`, `ietf.org`, `first.org`, `sans.org`, `verizon.com` (DBIR), `microsoft.com`, `google.com`, `cloudflare.com` |

2. Iterates all `<a href>` anchors (`links.py:29`). For each:
   - Strips whitespace; **skips** non-navigational hrefs: empty, or starting with `#`, `javascript:`, `mailto:`, `tel:` (`links.py:31`).
   - Resolves to an absolute URL via `absolute(url, href)` (`links.py:33`).
   - **Dedupes** by absolute URL using a `seen` set (`links.py:34-36`) — each distinct target counted once.
   - **Classifies** internal vs external via `same_site(abs_url, url)` (`links.py:38`), which compares registrable eTLD+1.
   - For external links it checks `_authority_domain(host_of(abs_url), authority_domains)`; on a match it records `{url, domain, text}` where `text` is the anchor text truncated to **200 chars** (`links.py:42-48`).

**`_authority_domain(host, domains) -> str | None`** (`links.py:61`) — returns the **most specific** configured domain a host belongs to, or `None`. A host matches a domain if `host == d` or `host.endswith("." + d)`. When several configured domains overlap (e.g. both `nist.gov` and `nvd.nist.gov`), it picks the longest match (`max(matches, key=len)`), making attribution **deterministic and independent of config ordering** (`links.py:62-69`). E.g. `nvd.nist.gov` is attributed to `nvd.nist.gov`, not `nist.gov`.

**Output keys** (`links.py:50-58`): `internal` (list of URLs), `external` (list), `authority` (list of `{url, domain, text}`), `internal_count`, `external_count`, `authority_count`, `authority_domains` (sorted unique set of matched domains).

**Inputs → outputs:** soup + page URL → link sets. **Side effects:** none. **Config:** `scoring.yaml: criteria.citation_signals.authority_domains` (note `extractors.yaml:23-25` documents that authority domains are deliberately *reused* from scoring.yaml rather than duplicated).

---

### 4.5 `extract/render_mode.py` — Render accessibility / JS-dependency detection

Detects pages whose content is injected by JavaScript and therefore invisible to AI crawlers that don't execute JS (`render_mode.py:1-8`). This is the "render-inflation" signal: a page where the rendered body has *far more* text than the initial HTML is JS-dependent.

**`extract(html, soup, url) -> dict[str, Any]`** (`render_mode.py:25`)

Algorithm:
1. Loads the `render_mode` config block (`render_mode.py:26-28`): `ratio_threshold = js_inflation_ratio` (default **3.0**), `min_initial = min_initial_text_chars` (default **200**).
2. **Approximates "initial HTML text"** from the *raw* `html` string by regex-stripping, in order (`render_mode.py:31-34`):
   - `<script>…</script>` blocks (`_SCRIPT_RE`, `render_mode.py:20`),
   - `<style>…</style>` blocks (`_STYLE_RE`, `render_mode.py:21`),
   - all remaining tags `<[^>]+>` (`_TAG_RE`, `render_mode.py:22`),
   then collapses whitespace and measures `initial_len`. This is a cheap shallow text-length estimate of what a no-JS fetch would see — deliberately not a full parse.
3. **Rendered text length:** `rendered_len = len(body_text(soup))` — the post-render body (`render_mode.py:36-37`). This call is **destructive** on the soup (chrome decompose).
4. **Inflation ratio:** `inflation = rendered_len / max(initial_len, 1)` (the `max(...,1)` guards div-by-zero) (`render_mode.py:39`).
5. **Two signals** combined (`render_mode.py:40-41`):
   - `js_only` = `initial_len < min_initial` **and** `rendered_len > min_initial` — the page shipped essentially no static text but has substantial rendered text (pure SPA shell).
   - `js_dependent` = `inflation >= ratio_threshold` **OR** `js_only` — either large inflation, or the js-only condition.

**Output keys** (`render_mode.py:43-49`): `initial_text_chars`, `rendered_text_chars`, `inflation_ratio` (rounded 2dp), `js_only_content`, `js_dependent`.

**Why these thresholds:** a 3× inflation means two-thirds of the visible content only exists after JS runs — a strong indicator a non-JS AI crawler would miss most of the page. The 200-char floor avoids flagging genuinely tiny pages where ratios are noisy.

**Config consumed — `extractors.yaml: render_mode`** (`config/extractors.yaml:54-58`):

```yaml
render_mode:
  js_inflation_ratio: 3.0
  min_initial_text_chars: 200
```

**Inputs → outputs:** raw HTML + rendered soup → render-mode flags. **Side effects:** mutates soup (decomposes chrome via `body_text`). No network/DB.

---

### 4.6 `extract/glossary.py` — Glossary / `DefinedTerm` opportunity detection

Flags pages where adding `DefinedTerm` schema would have outsized AEO impact. The module docstring (`glossary.py:1-7`) cites the audit finding: the Securin glossary has **132 definitions with zero `DefinedTerm` schema**.

**`extract(html, soup, url) -> dict[str, Any]`** (`glossary.py:16`)

Algorithm:
1. **`<dl>` definition lists** (`glossary.py:17-25`): for each `<dl>`, zip its `<dt>` terms with `<dd>` definitions (`strict=False` tolerates mismatched counts) into `{term, definition}` pairs; each definition text is truncated to **280 chars**.
2. **Heading-as-definition pattern** (`glossary.py:28-35`): counts `<h3>`/`<h4>` headings that look like a glossary term — the heading must be non-empty **and ≤ 6 words** (`len(text.split()) > 6` is rejected) — and whose immediate `find_next_sibling()` is a `<p>`. This catches "Term heading → definition paragraph" layouts that don't use semantic `<dl>`. The **6-word** cap is the heuristic separating short term-like headings from prose headings.
3. **Candidate decision** (`glossary.py:37-38`): `total = len(dl_pairs) + heading_defs`; `is_glossary_candidate = total >= 20`. The **20-definition** threshold means the page is dense enough with definitions to be treated as a glossary.
4. **Existing schema check** (`glossary.py:40-44`): scans every `<script type="application/ld+json">` block; if the literal string `"DefinedTerm"` appears in its contents, `has_defined_term_schema = True`. (Substring check, not full JSON-LD parse.)
5. **Opportunity** (`glossary.py:52`): `opportunity = is_glossary_candidate and not has_defined_term_schema` — a page that *is* a glossary but is *missing* the schema is precisely the high-value fix.

**Output keys** (`glossary.py:46-53`): `dl_pairs`, `heading_definition_count`, `total_definitions`, `is_glossary_candidate`, `has_defined_term_schema`, `opportunity`.

**Inputs → outputs:** soup → glossary-opportunity flags. **Side effects:** none (uses `find_all`/`get_text`/`find_next_sibling`, no decompose). **Config:** none — the `≥ 20` candidate threshold and `≤ 6` word cap are hardcoded constants.

---

### 4.7 `extract/pagespeed.py` — Google PageSpeed Insights client (criterion 8: Load Speed)

The only extractor that performs a **network call**. It queries Google's PageSpeed Insights v5 API and extracts Lighthouse performance metrics. The docstring (`pagespeed.py:1-6`) notes the free tier is ~25k queries/day and that the client is **disabled when `PSI_API_KEY` is unset**, in which case the scorer falls back to a neutral score.

Endpoint: `_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"` (`pagespeed.py:20`).

**`async fetch(url, strategy: str = "mobile") -> dict[str, Any] | None`** (`pagespeed.py:23`)

Algorithm:
1. `s = get_settings()`; if `s.psi_api_key` is falsy, **return `None`** immediately — the disabled path (`pagespeed.py:24-26`). The key is bound to settings field `psi_api_key` (`settings.py:180`), populated from env var **`AEO__PSI_API_KEY`** (the `Settings` model uses `env_prefix="AEO__"`, `settings.py:165`).
2. Builds query params `{url, strategy, category: "performance"}` (`pagespeed.py:27-31`). `strategy` is `"mobile"` by default (Google's default and the more demanding profile).
3. **Credential handling — security-critical** (`pagespeed.py:32-35`): the API key is sent via the **`x-goog-api-key` HTTP header**, NOT a `key=` query-string param. The inline comment explains the rationale: keeping the secret out of the request URL means it can never leak into a log through an exception's `str()` — an `httpx.HTTPStatusError` stringifies the full request URL, so a URL-embedded key would otherwise appear verbatim in error logs. Google APIs accept header auth, so this is functionally equivalent and safer.
   ```python
   headers = {"x-goog-api-key": s.psi_api_key}
   ```
4. Imports `async_transport` from `..crawl.transport` (lazy import, `pagespeed.py:36`) and opens an `httpx.AsyncClient` with a **60-second timeout** and the shared transport (so proxy/retry/transport config is consistent with the crawler) (`pagespeed.py:39`).
5. `GET`s the endpoint, calls `resp.raise_for_status()`, parses JSON (`pagespeed.py:40-42`).
6. **Failure handling** (`pagespeed.py:43-47`): any exception is caught and logged at **warning** level via structlog as event `psi_fetch_failed` with fields `url`, `status=_status_of(exc)`, and `error=type(exc).__name__`. It logs **only the status code and exception class name — never `str(exc)`** (which could carry the request URL / any query-string secret). Then returns `None` so the scorer degrades gracefully.
7. On success it digs into the Lighthouse result (`pagespeed.py:49-60`):
   - `perf = lighthouseResult.categories.performance.score` (a 0–1 float).
   - Returns a dict with `performance_score` rescaled to 0–100 via `round((perf or 0) * 100)` when `perf is not None` else `None`; plus four Core Web Vitals pulled from `lighthouseResult.audits`:

   | Output key | Lighthouse audit id | Unit |
   |---|---|---|
   | `performance_score` | `categories.performance.score` × 100 | 0–100 |
   | `lcp_ms` | `largest-contentful-paint` | ms |
   | `tbt_ms` | `total-blocking-time` | ms |
   | `cls` | `cumulative-layout-shift` | unitless |
   | `fcp_ms` | `first-contentful-paint` | ms |
   | `strategy` | (echoed input) | "mobile"/"desktop" |

**`fetch_sync(url, strategy="mobile") -> dict | None`** (`pagespeed.py:63`) — sync wrapper that runs the coroutine via `asyncio.run(fetch(...))` for callers not already in an event loop.

**`_status_of(exc) -> int | None`** (`pagespeed.py:68`) — pulls the HTTP status code off an httpx error if it carries a `.response`. Transport errors (timeout/connect) have no response, so it returns `None`. Used to enrich the failure log without exposing the URL.

**`_numeric_value(audit) -> float | None`** (`pagespeed.py:75`) — returns `audit["numericValue"]` if the audit dict is present, else `None`. Used to safely pull each Web Vital.

**Inputs → outputs:** page URL (+ strategy) → metrics dict, or `None` (disabled / failed). **Side effects:** outbound HTTPS GET to Google's PSI endpoint; emits a structlog `warning` on failure. No DB writes, no files. **Config / env:** `AEO__PSI_API_KEY` (settings `psi_api_key`); shared `async_transport()` from `crawl.transport`; hardcoded 60s timeout and `"performance"` category.

---

### Cross-cutting notes for the reader

- **Determinism:** every module except `pagespeed` is pure given the same HTML — no clocks, no randomness, no network. This makes them trivially unit-testable and reproducible across runs (the "deterministic-first" seam of the V4 design).
- **Soup mutation ordering:** `readability` and `render_mode` both call `body_text`, which decomposes chrome tags from the shared soup. If you add an extractor that needs `<nav>`/`<footer>`/`<script>` content, run it **before** these or parse a fresh soup.
- **Config single-sourcing:** authority domains live only in `scoring.yaml` and are reused by `links.py`; render thresholds and template-H1 patterns live in `extractors.yaml`. Hardcoded magic numbers that are *not* in YAML: readability's 50-word floor, the chunker's 1200-char target, glossary's ≥20 / ≤6-word heuristics, and PageSpeed's 60s timeout.

## 5. Scoring Core — Rubric, Result Math, Aggregator, Parallel Runner

This is the heart of the AEO scorer: the layer that turns one extracted page (an `ExtractionBundle`) into a single `PageScore`. The design philosophy, stated at the top of `src/aeo/scoring/__init__.py:1`, is **"8 criteria, deterministic-first, 1-5 scale"** — though the rubric has since grown to **10 criteria** (v3). "Deterministic-first" means every criterion produces a defensible score from parsed signals alone; an LLM, when enabled, only *refines* a few of them, it is never required.

The package splits cleanly into four concerns, each documented below:

| File | Responsibility |
|---|---|
| `rubric.py` | Load `config/scoring.yaml` once → the `Rubric` (criteria, weights, thresholds). |
| `result.py` | Shared scoring primitives: `ScoreContext` + the pure tier-math helpers. |
| `scorers/__init__.py` | The scorer registry (`SCORERS`), failure isolation, and the sequential/parallel runners. |
| `aggregator.py` | `score_page()` — orchestrate the run, apply weights, emit the `PageScore`. |

### 5.0 Package surface — `scoring/__init__.py`

`src/aeo/scoring/__init__.py:3-18` is a thin re-export façade. It pulls together the public API so callers `from aeo.scoring import score_page, load_rubric, ...` rather than reaching into submodules. The exported names are: `RUBRIC_VERSION`, `SCORERS`, `Criterion`, `Rubric`, `ScoreContext`, `load_rubric`, `priority_tier`, `run_all`, and `score_page`. (Note: `run_all_parallel` is intentionally *not* re-exported here — it is reachable via `aeo.scoring.scorers` and is wired internally by the aggregator.)

---

### 5.1 The rubric — `rubric.py`

`rubric.py` is the single source of truth for *what* the criteria are and *how* each maps a raw signal to a 1-5 tier. Scorers read their thresholds from `Criterion.cfg`, so **tuning the rubric never requires a code change** (`src/aeo/scoring/rubric.py:1-7`).

#### Data classes

```python
@dataclass(slots=True)
class Criterion:
    name: str        # registry key, e.g. "schema_markup"
    label: str       # human label, e.g. "Schema Markup"
    weight: float    # multiplier applied to the 1-5 tier in the total
    cfg: dict[str, Any]   # the raw YAML block for this criterion
```
(`src/aeo/scoring/rubric.py:18-23`)

```python
@dataclass(slots=True)
class Rubric:
    criteria: dict[str, Criterion]
    scale_min: int   # 1
    scale_max: int   # 5
    def get(self, name) -> Criterion   # criteria[name]; KeyError if absent
    def names() -> list[str]           # criterion keys in insertion order
```
(`src/aeo/scoring/rubric.py:26-36`)

Both use `slots=True` for compactness — the rubric is read on every page-scoring call.

#### `load_rubric() -> Rubric`

`src/aeo/scoring/rubric.py:39-58`. Decorated `@lru_cache(maxsize=1)`, so the YAML is parsed exactly **once per process** and the same `Rubric` object is handed out thereafter (callers must treat it as read-only).

Algorithm:
1. `raw = load_yaml_file("scoring.yaml")` — delegates to `aeo.settings.load_yaml_file`, itself `@cache`d, which reads `config/scoring.yaml` from the configured `config_dir` (`src/aeo/settings.py:242-250`). **No network, no DB** — pure file read.
2. Pull the `scale` block (defaults `min=1`, `max=5` if absent) and the `criteria` block (default empty).
3. For each criterion entry, build a `Criterion` with `label` defaulting to the key name, `weight` defaulting to `1.0`, and `cfg` set to the entire raw YAML block (so each scorer gets its own thresholds verbatim).
4. Return the assembled `Rubric`.

Robust to partial YAML: every `.get(...) or {}` guards against `None`/missing sections so a sparse config still yields a valid (if empty) rubric.

#### What `config/scoring.yaml` actually contains

The file (`config/scoring.yaml:1-116`) declares the **scale** and **10 criteria**. Header comment: weights default to 1.0 → equal weighting → **max 50**; criteria 1-8 are the shipped hard contract, 9-10 were added in v3.

Global scale (`config/scoring.yaml:6-8`):
```yaml
scale: { min: 1, max: 5 }
```

The full criteria set, in YAML (= registry) order, with each one's weight and the concrete knobs each scorer consumes:

| # | Criterion (key) | Label | Weight | Key thresholds / vocab (actual values) |
|---|---|---|---|---|
| 1 | `schema_markup` | Schema Markup | 1.0 | `valued_types`: FAQPage, HowTo, TechArticle, Article, NewsArticle, Organization, DefinedTerm, ItemList, BreadcrumbList, Product, BlogPosting |
| 2 | `qa_blocks` | Q&A Blocks | 1.0 | `min_answer_chars: 80`; `question_words`: what, why, how, when, where, who, which, is, are, do, does, can, should |
| 3 | `stats_in_html` | Stats in HTML | 1.0 | `tiers: {1:0, 2:1, 3:3, 4:6, 5:10}` (count of distinct numeric claims) |
| 4 | `entity_consistency` | Entity Consistency | 1.0 | `tiers: {1:0.0, 2:0.5, 3:1.0, 4:1.5, 5:2.5}` (ratio of canonical-entity to first-person mentions; >1 good) |
| 5 | `heading_structure` | Heading Structure | 1.0 | `tiers: {1:0.0, 2:0.10, 3:0.25, 4:0.45, 5:0.65}` (% of H2/H3 that are questions or named concepts); `penalty_missing_h1: 1`; `penalty_template_h1: 1` |
| 6 | `content_depth` | Content Depth | 1.0 | `min_word_count_for_credit: 400`; `methodology_keywords`: methodology, dataset, sample size, n=, study, research, analysis, findings, results, evidence, framework, protocol (LLM fills a `judgement` field when enabled) |
| 7 | `citation_signals` | Citation Signals (E-E-A-T) | 1.0 | `authority_domains` (12: nist.gov, cisa.gov, nvd.nist.gov, mitre.org, owasp.org, ietf.org, first.org, sans.org, verizon.com, microsoft.com, google.com, cloudflare.com); `credentials` (CISSP, OSCP, OSCE, CEH, GIAC, GPEN, GWAPT, GCIH, GCFA, CISA, CISM, CRISC, CCSP, OSWE, OSED); per-tier `tiers` table of `{author, date, authority_links}` (see below) |
| 8 | `load_speed` | Load Speed | 1.0 | `tiers: {1:0, 2:30, 3:50, 4:75, 5:90}` (PSI mobile perf 0-100); `penalty_js_only_content: 1` |
| 9 | `render_accessibility` | Render Accessibility | 1.0 | `inflation_max: {5:1.5, 4:2.5, 3:4.0, 2:8.0}` (rendered/initial text ratio; LOWER better); `min_initial_text_chars: 200` |
| 10 | `answer_readability` | Answer Readability | 1.0 | `flesch_tiers: {1:0, 2:20, 3:30, 4:45, 5:55}`; `max_avg_sentence_len: 28`; `min_chunks_for_credit: 3`; `min_word_count: 50` |

The `citation_signals` tiers are a per-tier *struct* rather than a single number (`config/scoring.yaml:78-83`):
```yaml
tiers:
  1: { author: false, date: false, authority_links: 0 }
  2: { author: false, date: false, authority_links: 1 }
  3: { author: false, date: true,  authority_links: 2 }
  4: { author: true,  date: true,  authority_links: 3 }
  5: { author: true,  date: true,  authority_links: 5 }
```
i.e. tier 5 demands an author byline, a date, AND ≥5 authority links.

**Why all weights are 1.0:** with 10 criteria each scored 1-5 and weight 1.0, the maximum weighted total is `10 × 5 × 1.0 = 50`. The header comment (`config/scoring.yaml:1-4`) notes a higher weight biases the total; the aggregator derives `max_possible` from the weights, so changing one weight automatically rescales the ceiling (see §5.4).

---

### 5.2 Result math & the score context — `result.py`

`result.py` holds the only input a scorer receives plus the small pure helpers for mapping signals onto the 1-5 scale. It is deliberately dependency-light (only `LLMClient`, `ExtractionBundle`, `Rubric`) so both scorers and the aggregator import it without a cycle (`src/aeo/scoring/result.py:1-7`).

#### `ScoreContext`

```python
@dataclass(slots=True)
class ScoreContext:
    bundle: ExtractionBundle      # the extracted page (see §below)
    rubric: Rubric                # shared, read-only
    llm: LLMClient | None = None  # optional; disabled clients no-op
```
(`src/aeo/scoring/result.py:19-26`)

This is the entire contract a scorer is allowed to see. The docstring is explicit about the discipline: **scorers read the bundle only — they never re-parse HTML or hit the network, except the optional LLM.** The `bundle` is an `ExtractionBundle` (`src/aeo/storage/models.py:52-60`): a `page_id` plus a `data: dict[str, Any]` keyed by extractor name, accessed via `bundle.get(name, default)`. Because the context is read-only and shared, scorers are effectively pure functions over it — which is exactly what makes the parallel runner safe (§5.3).

#### `clamp_tier(n, lo=1, hi=5) -> int`

`src/aeo/scoring/result.py:29-30`. Rounds a float and clamps it into `[lo, hi]`:
```python
return max(lo, min(hi, round(n)))
```
Scorers use this when they compute a tier arithmetically (e.g. after subtracting penalties) and must guarantee the result stays a legal 1-5 integer.

#### `tier_from_thresholds(value, tiers) -> int`

`src/aeo/scoring/result.py:33-40`. The workhorse mapper. `tiers` maps **tier → minimum value** in ascending order, e.g. `{1:0, 2:1, 3:3, 4:6, 5:10}` (the `stats_in_html` table). It returns the **highest tier whose minimum threshold the value meets or exceeds**:
- `best` starts at the smallest tier key (the floor — so a value below every threshold still returns a valid tier, normally 1).
- It iterates tiers in ascending numeric order and, for each, sets `best = tier` whenever `value >= tiers[tier]`.
- Because iteration is ascending, the last (highest) qualifying tier wins.

Example with the stats table: 4 distinct numbers → meets tier-1 (0), tier-2 (1), tier-3 (3), but not tier-4 (6) → returns **3**. This single helper backs every criterion whose YAML exposes a flat `tiers`/`*_tiers`/`inflation_max` map.

#### `priority_tier(total, max_possible) -> str`

`src/aeo/scoring/result.py:43-52`. Converts the final numeric total into a **remediation priority**, with the deliberate inversion that *a low score is a high priority to fix*. It computes `pct = total / max_possible * 100` (guarding divide-by-zero → `0.0`) and bands it:

| Score % of max | Priority returned |
|---|---|
| `< 35%` | `critical` |
| `35% – < 55%` | `high` |
| `55% – < 75%` | `medium` |
| `≥ 75%` | `low` |

So on the max-50 scale, a total below ~17 is `critical`, below ~27 is `high`, below ~37 is `medium`, and 37+ is `low`. This string is what downstream review/triage uses to queue the worst pages first.

---

### 5.3 Scorer registry & runners — `scorers/__init__.py`

This module wires the rubric keys to actual scoring functions and provides failure isolation plus the two execution modes.

#### `SCORERS` — the hard contract

`src/aeo/scoring/scorers/__init__.py:36-47` is a dict mapping each criterion name to its `score(ctx) -> CriterionScore` function, imported from the ten sibling modules (`schema_markup`, `qa_blocks`, `stats_in_html`, `entity_consistency`, `heading_structure`, `content_depth`, `citation_signals`, `load_speed`, `render_accessibility`, `answer_readability`).

```python
SCORERS: dict[str, Callable[[ScoreContext], CriterionScore]] = {
    "schema_markup": schema_markup.score,
    ... 8 more ...
    "answer_readability": answer_readability.score,
}
```

The keys are a **hard contract** (`src/aeo/scoring/scorers/__init__.py:2-8`, `33-35`): `storage/repos/scores.py` indexes the resulting dict by these exact names to fill the `rubric_scores_v2` columns. Criteria 1-8 are the shipped contract; criteria 9-10 (`render_accessibility`, `answer_readability`) were added in v3 with **migration 0008** adding their columns. **Adding a criterion = add a module + register it here + add a column/migration** — the registry order also defines the canonical output order (see the parallel runner).

Each scorer returns a `CriterionScore` (`src/aeo/storage/models.py:63-69`): `name`, `value` (the 1-5 int), `evidence` (dict of the signals that justified it), `notes`, and `scored_by` (`"deterministic"` | a model name | `"hybrid"`).

#### `_run_one(name, fn, ctx) -> CriterionScore` — failure isolation

`src/aeo/scoring/scorers/__init__.py:50-63`. The single choke-point through which **every** scorer is invoked (both sequential and parallel paths call it). It runs `fn(ctx)` inside a broad `try/except Exception`. On a raise it:
1. Logs a structured `warning` event `scorer_failed` with `criterion`, `page_id`, and the error string (`log = get_logger(__name__)`, `src/aeo/scoring/scorers/__init__.py:31`). **Side effect: a log line, nothing else.**
2. Returns a *floored* `CriterionScore`:
   ```python
   CriterionScore(name=name, value=ctx.rubric.scale_min,  # = 1
                  evidence={"error": str(exc)},
                  notes="scorer error → floored",
                  scored_by="error")
   ```

This is the **"one scorer failing floors + records-error, never aborts the page"** contract: a buggy or LLM-timeout scorer drops that one criterion to tier 1 (the minimum) with the error captured in `evidence`/`scored_by="error"`, and the other nine still produce real scores. A single bad page can never crash an entire crawl run.

#### `run_all(ctx) -> dict[str, CriterionScore]` — sequential

`src/aeo/scoring/scorers/__init__.py:66-69`. A dict comprehension over `SCORERS.items()`, each routed through `_run_one`:
```python
return {name: _run_one(name, fn, ctx) for name, fn in SCORERS.items()}
```
Output preserves the `SCORERS` insertion order. This is the default, v3 behavior.

#### `run_all_parallel(ctx, *, max_workers=8) -> dict[str, CriterionScore]` — the v4 Parallel Processor

`src/aeo/scoring/scorers/__init__.py:72-86`. Runs the scorers concurrently in a `ThreadPoolExecutor`. Its design guarantees **byte-identical output to `run_all`**:

```python
items = list(SCORERS.items())
workers = max(1, min(max_workers, len(items)))     # never more threads than scorers
with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scorer") as pool:
    scored = list(pool.map(lambda it: (it[0], _run_one(it[0], it[1], ctx)), items))
by_name = dict(scored)
return {name: by_name[name] for name, _ in items}   # fixed order = determinism
```

Key properties and the *why*:
- **Worker cap** (`src/aeo/scoring/scorers/__init__.py:82`): `max(1, min(max_workers, len(items)))` — at least 1, never more than the 10 scorers, so an over-large `max_workers` doesn't spawn idle threads. With the default 8 and 10 scorers, you get 8 workers.
- **Failure isolation preserved**: each task still goes through `_run_one`, so a raising scorer floors-and-records exactly as in the sequential path — the pool never sees an exception, so it never aborts the page.
- **Determinism / fixed order** (`src/aeo/scoring/scorers/__init__.py:84-86`): `pool.map` may *complete* tasks out of order, but the results are collected into `by_name` and then **re-assembled in the original `SCORERS` order**. The returned dict's key order is therefore identical to `run_all`'s, which matters because the hard 10-key contract and the `rubric_scores_v2` column mapping depend on it.
- **Why it's safe to parallelize at all** (`src/aeo/scoring/scorers/__init__.py:74-80`): scorers are pure functions over a shared, *read-only* `ScoreContext` — they never mutate the bundle — so there is no shared mutable state and no need for locks.
- **Where the speedup comes from**: the I/O-bound, LLM-refined criteria (the docstring names `content_depth` and stats). Their model HTTP calls release the GIL while in flight, so threads overlap usefully; CPU-bound scorers "see no penalty worth measuring." Threads (not processes) are correct here precisely because the costly work is network I/O, not CPU.

---

### 5.4 Aggregation — `aggregator.py`

`aggregator.py` turns the per-criterion scores into one `PageScore` (`src/aeo/scoring/aggregator.py:1-8`). The total is the **weighted sum of the 1-5 tiers**; per-criterion tiers are stored raw (1-5) and only the *total* reflects weighting. `max_possible` is derived from the rubric so it tracks the criterion count automatically.

#### `RUBRIC_VERSION`

`src/aeo/scoring/aggregator.py:18`:
```python
RUBRIC_VERSION = "2.0"  # v3 build: 10 criteria (was "1.0" = 8 criteria)
```
Stamped onto every `PageScore` so historical scores remain interpretable after the rubric grew from 8 to 10 criteria.

#### `score_page(bundle, run_id, *, llm=None, rubric=None, parallel=False, max_workers=8) -> PageScore`

`src/aeo/scoring/aggregator.py:21-56`. The public entry point of the entire scoring subsystem.

Signature / inputs → output:
- **Inputs**: `bundle: ExtractionBundle` (the extracted page), `run_id: int` (stamped onto the result for the crawl run); keyword-only `llm`, `rubric`, `parallel`, `max_workers`.
- **Output**: a `PageScore` (`src/aeo/storage/models.py:72-80`): `page_id`, `run_id`, `criteria` (the name→`CriterionScore` dict), `total`, `max_possible`, `priority_tier`, `rubric_version`.
- **Side effects**: none directly in this function — no DB writes, no files. It only *reads* (`load_rubric` → file read once; optional LLM HTTP calls inside scorers). Persisting the `PageScore` is the caller's job (the `scores` repo).

Algorithm, step by step:

1. **Resolve the rubric** (`aggregator.py:30`): `rubric = rubric or load_rubric()` — caller may inject one (tests), else the cached singleton.

2. **Resolve the LLM client** (`aggregator.py:31-32`): if `llm is None`, `llm = get_client()`, which is `LLMClient(get_settings().llm)` (`src/aeo/nlp/llm.py:202-203`). The comment notes **disabled clients no-op; scorers handle that** — so deterministic-first holds even when `llm.enabled` is false (config `LLMCfg.enabled`, `src/aeo/settings.py:83`).

3. **Build the context** (`aggregator.py:34`): `ctx = ScoreContext(bundle=bundle, rubric=rubric, llm=llm)`.

4. **Run the scorers** (`aggregator.py:35-36`) — the parallel switch:
   ```python
   criteria = run_all_parallel(ctx, max_workers=max_workers) if parallel else run_all(ctx)
   ```
   `parallel` is **opt-in** and the comment promises *identical output, faster on LLM criteria*. The two flags are surfaced in config as `ScoringCfg.parallel` (default `False`) and `ScoringCfg.max_workers` (default `8`) (`src/aeo/settings.py:133-138`); the caller passes them through to `score_page`.

5. **Weighted total + max ceiling** (`aggregator.py:38-46`):
   ```python
   total = 0.0; max_possible = 0.0
   for name, crit in criteria.items():
       weight = rubric.get(name).weight if name in rubric.criteria else 1.0
       total += crit.value * weight
       max_possible += rubric.scale_max * weight
   ```
   - Each criterion's 1-5 `value` is multiplied by its `weight` and summed into `total`.
   - In lock-step, `max_possible` accrues `scale_max × weight` for each criterion — i.e. the score that criterion would contribute if it were perfect. Computing the ceiling from the *same loop and the same weights* guarantees `total ≤ max_possible` always and that the ceiling tracks the criterion count automatically (no hard-coded 50).
   - **Defensive fallback**: a criterion present in `criteria` but absent from the rubric is weighted `1.0` rather than raising — so a stray scorer key still contributes sanely.
   - With the shipped config (10 criteria × weight 1.0, `scale_max=5`), `max_possible = 50`.

6. **Round to integers** (`aggregator.py:45-46`): `total_i = round(total)`, `max_i = round(max_possible)`. (Rounding matters only if a non-1.0 fractional weight is configured; with the default weights both are already whole.)

7. **Assemble & return** (`aggregator.py:48-56`):
   ```python
   return PageScore(
       page_id=bundle.page_id, run_id=run_id,
       criteria=criteria, total=total_i, max_possible=max_i,
       priority_tier=priority_tier(total_i, max_i),
       rubric_version=RUBRIC_VERSION,
   )
   ```
   The `priority_tier` is computed from the integer total via the banding in §5.2, and `RUBRIC_VERSION = "2.0"` is stamped on.

#### End-to-end summary

`score_page` is the contract boundary: hand it an `ExtractionBundle` + `run_id`, optionally flip `parallel=True`, and it returns a fully-formed `PageScore` with raw per-criterion tiers, a weighted total out of (default) 50, and a remediation priority — without ever crashing on a single misbehaving criterion (each is floored-and-logged in isolation) and without writing anything itself.

## 6. The Ten Scorers — One Criterion at a Time

This is the heart of the product. Every page is graded against ten criteria, each producing an integer tier from **1 (worst) to 5 (best)**. Each scorer is a single pure module exposing one public function `score(ctx: ScoreContext) -> CriterionScore`. The aggregator (covered elsewhere) calls each in turn and sums the tiers (weights default to `1.0`, so the max total is `10 × 5 = 50`).

This section documents, for each scorer: the extractor signals it reads, the exact tier rule (with thresholds quoted from `config/scoring.yaml`), the evidence/notes it records, and — for the two hybrid scorers — where and how the LLM refines the result.

### 6.0 Shared contract — `ScoreContext`, `CriterionScore`, and the tier helpers

Every scorer receives exactly one input and returns exactly one output. It never re-parses HTML and never hits the network except through the optional LLM.

**`ScoreContext`** — `src/aeo/scoring/result.py:19`

```python
@dataclass(slots=True)
class ScoreContext:
    bundle: ExtractionBundle      # dict of all extractor outputs, read via .get(key)
    rubric: Rubric                # parsed config/scoring.yaml
    llm: LLMClient | None = None  # present & .enabled only when Ollama is up
```

- `ctx.bundle.get("<extractor_key>", {})` is how a scorer reads upstream signals — e.g. `ctx.bundle.get("schema_jsonld", {})`.
- `ctx.rubric.get("<criterion>").cfg` returns that criterion's raw YAML config dict (thresholds, vocabularies, penalties). See `src/aeo/scoring/rubric.py:32` and `Criterion.cfg` at `rubric.py:23`.
- `ctx.llm` is consulted by only two scorers (`stats_in_html`, `content_depth`); all others are purely deterministic.

**`CriterionScore`** — `src/aeo/storage/models.py:64`

```python
@dataclass(slots=True)
class CriterionScore:
    name: str
    value: int                       # the 1-5 tier
    evidence: dict[str, Any] = {}    # structured proof, persisted with the score
    notes: str = ""                  # human-readable one-liner
    scored_by: str = "deterministic" # 'deterministic' | 'hybrid' | 'pagespeed'
```

`scored_by` is provenance: it records whether the tier came purely from rules, from a deterministic-LLM blend (`"hybrid"`), or from an external API (`"pagespeed"`).

**The two tier helpers** — `src/aeo/scoring/result.py`

| Helper | Location | Behaviour |
|---|---|---|
| `clamp_tier(n, lo=1, hi=5)` | `result.py:29` | `max(lo, min(hi, round(n)))` — rounds a float and clamps into `[1,5]`. Used wherever penalties/bonuses or an average could push a tier out of range. |
| `tier_from_thresholds(value, tiers)` | `result.py:33` | Given an **ascending** `tier→min-value` map, returns the **highest** tier whose minimum threshold `value` meets or exceeds. Iterates ascending and keeps the last tier where `value >= tiers[tier]`. |

`tier_from_thresholds` is the workhorse for the "more is better" criteria (stats, entity ratio, heading-question ratio, PageSpeed score, Flesch). Two scorers (`citation_signals`, `render_accessibility`) implement their own threshold walks because their semantics differ (compound requirements / lower-is-better).

A note on config defaults: every scorer reads its thresholds via `crit.cfg.get("tiers", {…hardcoded fallback…})`. The hardcoded fallbacks in code match the YAML values exactly, so editing `config/scoring.yaml` retunes the product with no code change.

---

### 6.1 Criterion 1 — Schema Markup (`schema_markup.py`)

**Reads:** `bundle["schema_jsonld"]` (`types`, `block_count`, `invalid_blocks`) and `bundle["glossary"]` (`opportunity`).
**Config:** `crit.cfg["valued_types"]` — the high-value JSON-LD allow-list.

The tier is driven by how many **high-value** JSON-LD types are present. "High-value" means the intersection of the page's declared `@type`s with the `valued_types` vocabulary from YAML (`scoring.yaml:15`):

> `[FAQPage, HowTo, TechArticle, Article, NewsArticle, Organization, DefinedTerm, ItemList, BreadcrumbList, Product, BlogPosting]`

Let `n_valued = len(types_present ∩ valued_types)`. Base tier (`schema_markup.py:28`):

| Condition | Tier |
|---|---|
| `block_count == 0` (no structured data at all) | 1 |
| `n_valued == 0` (schema exists but none high-value) | 2 |
| `n_valued == 1` | 3 |
| `n_valued == 2` | 4 |
| `n_valued >= 3` | 5 |

**Malformed-block penalty** (`schema_markup.py:39`): if `invalid_blocks > 0` **and** the tier is currently above 1, subtract one point. The design intent (per the module docstring) is that malformed JSON-LD is a *defect*, not merely a *miss* — a broken block is worse than an absent one because it signals sloppy implementation.

**Evidence recorded:** `block_count`, `invalid_blocks`, full sorted `types_present`, `valued_types_present`, and `defined_term_opportunity` (bool from the glossary extractor).
**Notes:** built from "No structured data found", "{n} malformed JSON-LD block(s)", and — the flagged quick win — "Glossary page lacks DefinedTerm schema (high-impact quick win)" when `glossary.opportunity` is true. Falls back to "{n} high-value schema type(s)".

The glossary `DefinedTerm` gap is surfaced as evidence even though it does not change the tier — it is highlighted as the audit's single biggest quick win.

---

### 6.2 Criterion 2 — Q&A Blocks (`qa_blocks.py`)

**Reads:** `bundle["qa_blocks"]` (`pair_count`, `question_heading_count`, `qa_pairs`) and `bundle["schema_jsonld"]` (`types`).
**Config:** none consumed directly by the scorer; the extractor enforces `min_answer_chars: 80` and the `question_words` list (`scoring.yaml:23-24`) when it counts genuine pairs.

`pair_count` is the count of **real** question→answer pairs — a question heading followed by a substantive paragraph (the extractor already discards answers shorter than 80 chars). Base tier (`qa_blocks.py:23`):

| `pair_count` | Tier |
|---|---|
| 0 | 1 |
| 1 | 2 |
| 2 | 3 |
| 3–4 | 4 |
| ≥5 | 5 |

**FAQPage bonus** (`qa_blocks.py:34`): if the page declares `FAQPage` in its JSON-LD `types`, add one tier via `clamp_tier(value + 1)` (so a page already at 5 stays at 5). Explicit FAQ schema is a strong, machine-readable AEO signal, so it earns the bump on top of the prose-pair count.

**Evidence:** `pair_count`, `question_heading_count`, `has_faqpage_schema`, and up to 5 `sample_questions` (the question text of the first five pairs).
**Notes:** flags the common failure mode "{n} question heading(s) but no substantive answers" when `pairs == 0` but headings exist (i.e. questions posed but never answered), plus "FAQPage schema present". Falls back to "{n} Q&A pair(s)".

---

### 6.3 Criterion 3 — Stats in HTML (`stats_in_html.py`) — *hybrid*

**Reads:** `bundle["stats"]` (`count`, `by_kind`, `hits`).
**Config:** `crit.cfg["tiers"]` = `{1: 0, 2: 1, 3: 3, 4: 6, 5: 10}` (`scoring.yaml:30`).

This counts distinct concrete numeric claims and maps the count through `tier_from_thresholds`:

| Effective distinct stats | Tier |
|---|---|
| 0 | 1 |
| 1–2 | 2 |
| 3–5 | 3 |
| 6–9 | 4 |
| ≥10 | 5 |

**LLM as a disqualifier** (`stats_in_html.py:32`): when `ctx.llm` is present, enabled, and `raw_count > 0`, the scorer calls `_llm_disqualify` and uses the **lower** of the two counts (`effective = kept`), flipping `scored_by` to `"hybrid"`. The LLM can only *reduce* the count, never inflate it — it is a precision filter, not a recall booster.

`_llm_disqualify(llm, hits)` (`stats_in_html.py:56`):
- Takes the first 25 `hits`, formats each as "`value (context: …first 80 chars…)`".
- Prompts the model to count only **genuine quantitative statistics** (e.g. "43% of breaches", "2.5x faster", "$4.2M average cost") and to explicitly **exclude** calendar dates, years, phone numbers, page/section numbers, product prices, and software version numbers.
- Demands strict JSON `{"genuine": <integer>}`.
- The returned integer is bounded to `[0, len(hits)]` via `max(0, min(len(hits), int(...)))`. If the model returns nothing parseable or a non-numeric `genuine`, it returns `None` and the deterministic `raw_count` stands.

**Evidence:** `raw_count`, `effective_count`, `llm_genuine_count` (None when LLM didn't run), `by_kind` breakdown, and up to 10 sample `value`s.
**Notes:** "{effective} distinct statistic(s)".

---

### 6.4 Criterion 4 — Entity Consistency (`entity_consistency.py`)

**Reads:** `bundle["entities"]` (`ratio`, `entity_count`, `first_person_count`, `primary`).
**Config:** `crit.cfg["tiers"]` = `{1: 0.0, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.5}` (`scoring.yaml:37`).

`ratio` is canonical-entity mentions ÷ first-person mentions ("we/our/us"). The interpretation (from the YAML comment): `1.0` = balanced, `>1.0` = entity-dominant (good — the page names itself, which answer engines can attribute), `<1.0` = first-person-dominant (bad). Three branches (`entity_consistency.py:26`):

| Condition | Tier | Note |
|---|---|---|
| `entity_count == 0 and first_person == 0` | **1** | "Page never names the entity" — the page mentions neither the brand nor itself. |
| `ratio is None` (entity present, **zero** first-person) | **5** | "Entity named with no first-person language" — fully entity-dominant; division by zero is treated as the best case, not an error. |
| otherwise | `tier_from_thresholds(ratio, tiers)` | "entity:first-person ratio = {ratio:.2f}" |

So with a real ratio: `<0.5`→1, `0.5–<1.0`→2, `1.0–<1.5`→3, `1.5–<2.5`→4, `≥2.5`→5.

The docstring records the motivating finding: Securin pages run first-person roughly 2:1 over the brand name (ratio ≈ 0.5), which lands them at tier 2 — exactly the failure this criterion is tuned to catch.

**Evidence:** `primary_entity` (name), `entity_count`, `first_person_count`, `ratio`.

---

### 6.5 Criterion 5 — Heading Structure (`heading_structure.py`)

**Reads:** `bundle["headings"]` (`h23_question_ratio`, `missing_h1`, `template_h1`, `h1_text`, `h23_total`, `hierarchy_ok`).
**Config:** `crit.cfg["tiers"]` = `{1: 0.0, 2: 0.10, 3: 0.25, 4: 0.45, 5: 0.65}` plus `penalty_missing_h1: 1` and `penalty_template_h1: 1` (`scoring.yaml:43-46`).

**Base tier** from the share of H2/H3 headings phrased as questions (`tier_from_thresholds(ratio, tiers)`):

| H2/H3 question ratio | Base tier |
|---|---|
| `< 0.10` | 1 |
| `0.10–<0.25` | 2 |
| `0.25–<0.45` | 3 |
| `0.45–<0.65` | 4 |
| `≥0.65` | 5 |

**Two penalties subtracted from the base** (`heading_structure.py:28`), then `clamp_tier` re-floors to ≥1:
- `missing_h1` → −1 (`penalty_missing_h1`).
- `template_h1` → −1 (`penalty_template_h1`) — a generic/boilerplate H1 such as literally "Resources" or "Welcome", or just the page name.

Both penalties target concrete audit findings (per docstring): the Zero-Days page has no H1, and several product pages render the literal H1 "Resources".

**Evidence:** `h1_text`, `h23_total`, `h23_question_ratio` (rounded to 3 dp), `base_tier` (the pre-penalty tier, so a reviewer can see what the penalties cost), the list of `penalties` applied, and `hierarchy_ok`.
**Notes:** the penalty list (e.g. `missing_h1; template_h1 ('Resources')`) or, if clean, "{ratio:.0%} question headings".

---

### 6.6 Criterion 6 — Content Depth (`content_depth.py`) — *the primary hybrid scorer*

**Reads:** `bundle["readability"]` (`word_count`), `bundle["stats"]` (`count`), `bundle["headings"]` (`h23_total`), `bundle["chunker"]` (`chunks`, for reconstructed text), and `bundle["meta"]` (`title`, for the LLM prompt).
**Config:** `min_word_count_for_credit: 400` and the `methodology_keywords` list (`scoring.yaml:53-55`):

> `[methodology, dataset, sample size, n=, study, research, analysis, findings, results, evidence, framework, protocol]`

This scorer operates on the **chunker's reconstructed text** (`_chunk_text`, `content_depth.py:89` — joins all chunk `text` fields) so it never re-parses HTML.

**Step 1 — deterministic base from word count** (`content_depth.py:42`):

| Word count | Base |
|---|---|
| `< 200` | 1 |
| `200–<400` (below credit threshold) | 2 |
| `400–<800` | 3 |
| `800–<1500` | 4 |
| `≥1500` | 5 |

**Step 2 — deterministic adjustments** (`content_depth.py:54`):
- **+1** (clamped) if `method_hits >= 2` **or** `stats_count >= 6`. `method_hits` is the count of *distinct* methodology keywords found in the lowercased text (`_distinct_keyword_hits`, `content_depth.py:95`). This rewards pages that show their work (research/methodology language) or are statistically dense.
- **−1** (clamped) if the tone is `promotional` **and** `wc < 800`. Promotional tone comes from `tone.analyze(text[:20000])` (`_TONE_CONTENT_CAP = 20000`); the penalty only fires on *short* marketing copy, since a long page can be both promotional and genuinely deep.

This yields `det`, the deterministic tier.

**Step 3 — LLM blend** (`content_depth.py:63`): when `ctx.llm` is enabled, `wc >= 50`, and text exists, `_llm_depth` asks the model for an independent 1–5 `depth_score`. If valid, the final tier is the **average of the deterministic and LLM tiers**, clamped: `value = clamp_tier((det + llm_tier) / 2)`, and `scored_by = "hybrid"`. If Ollama is absent or the model returns nothing usable, the deterministic `det` stands — the LLM **never blocks a run**.

`_llm_depth` (`content_depth.py:100`) loads the externalized prompt via `load_prompt("content_depth")` and substitutes `<<TITLE>>` (meta title, ≤200 chars), `<<WORD_COUNT>>`, and `<<CONTENT>>` (text capped at `_LLM_CONTENT_CAP = 4000` chars). Note the asymmetry: tone analysis sees up to 20 000 chars, but the LLM prompt is capped at 4 000 to control token cost.

**Evidence:** `word_count`, `deterministic_base` (which here is `det` *after* the ±1 adjustments — the variable passed is `det`, not the raw length base), `methodology_keyword_hits`, `stats_count`, `h23_total`, `promotional`, up to 10 `promotional_phrases`, and the raw `llm_judgement` object.
**Notes:** assembled from "{wc} words", "below {min}-word credit threshold", "promotional tone", and "LLM-assisted" (when hybrid).

---

### 6.7 Criterion 7 — Citation Signals / E-E-A-T (`citation_signals.py`)

**Reads:** `bundle["eeat"]` (`has_author`, `has_date`, `author`, `publish_date`, `credentials_mentioned`), `bundle["links"]` (`authority_count`, `authority_domains`), and `bundle["schema_jsonld"]` (`authors`, `dates`) as fallback.
**Config:** `crit.cfg["tiers"]` — a **compound requirements** table (`scoring.yaml:78-83`); also `authority_domains` and `credentials` vocabularies (`scoring.yaml:61-76`).

This scorer does **not** use `tier_from_thresholds`. Each tier specifies a *set* of requirements that must **all** be satisfied; the page earns the **highest tier whose requirements are fully met** (`citation_signals.py:30` — walks tiers descending and `break`s on the first match; defaults to 1 if none match).

| Tier | author | date | authority links ≥ |
|---|---|---|---|
| 1 | false | false | 0 |
| 2 | false | false | 1 |
| 3 | false | true | 2 |
| 4 | true | true | 3 |
| 5 | true | true | 5 |

Inputs (`citation_signals.py:24`):
- `has_author = eeat.has_author OR schema.authors` — on-page byline, else JSON-LD author.
- `has_date = eeat.has_date OR schema.dates` — on-page date, else JSON-LD date.
- `authority = links.authority_count` — count of outbound links to the `authority_domains` allow-list (NIST, CISA, NVD, MITRE, OWASP, IETF, FIRST, SANS, Verizon/DBIR, Microsoft, Google, Cloudflare).

The requirement check (`citation_signals.py:32`) skips a tier if it requires an author/date the page lacks, or if `authority < req.authority_links`. So a page with author + date but only 3 authority links lands at tier 4, not 5.

**Evidence:** `has_author`, `author`, `has_date`, `publish_date`, `authority_link_count`, `authority_domains` (the actual domains hit), and `credentials_mentioned` (security certs like CISSP/OSCP/CEH detected in body text per the `credentials` vocabulary).
**Notes:** the missing-signals list ("no author", "no date", "no authority links") or "author + date + authority links present". The docstring records that Securin pages have zero authority links and no bylines → tier 1.

---

### 6.8 Criterion 8 — Load Speed (`load_speed.py`)

**Reads:** `bundle["pagespeed"]` (`performance_score`, `lcp_ms`, `tbt_ms`, `cls`) and `bundle["render_mode"]` (`js_only_content`, `inflation_ratio`).
**Config:** `crit.cfg["tiers"]` = `{1: 0, 2: 30, 3: 50, 4: 75, 5: 90}` and `penalty_js_only_content: 1` (`scoring.yaml:89-91`).

The base tier maps the **PageSpeed Insights mobile performance score (0–100)** via `tier_from_thresholds`:

| PSI mobile perf | Tier |
|---|---|
| `< 30` | 1 |
| `30–<50` | 2 |
| `50–<75` | 3 |
| `75–<90` | 4 |
| `≥90` | 5 |

**Neutral when PSI is unavailable** (`load_speed.py:30`): if there is no `pagespeed` bundle or `performance_score is None` (no API key configured), the tier is set to `_NEUTRAL = 3` and `scored_by = "deterministic"`, with note "PageSpeed unavailable — neutral score". Missing data is treated as neutral rather than punitive — a missing speed measurement shouldn't tank a page's overall grade. When PSI *is* available, `scored_by = "pagespeed"`.

**JS-only penalty** (`load_speed.py:39`): regardless of whether PSI ran, if `render_mode.js_only_content` is true, dock one point via `clamp_tier(value - 1)`. Rationale (docstring): content an AI crawler can't see without executing JS is an AEO problem independent of raw speed.

**Evidence:** `psi_available`, `performance_score`, Core Web Vitals (`lcp_ms`, `tbt_ms`, `cls`), `js_only_content`, and `inflation_ratio`.
**Notes:** "PSI mobile performance {n}" (or the unavailable message) plus "JS-only content penalty" when applicable.

---

### 6.9 Criterion 9 — Render Accessibility (`render_accessibility.py`) — *v3*

**Reads:** `bundle["render_mode"]` (`js_only_content`, `js_dependent`, `inflation_ratio`, `initial_text_chars`, `rendered_text_chars`).
**Config:** `crit.cfg["inflation_max"]` = `{5: 1.5, 4: 2.5, 3: 4.0, 2: 8.0}` (`scoring.yaml:100`).

This criterion asks: *can an answer engine read the content from raw HTML, without executing JS?* It uses a **lower-is-better** threshold walk, the inverse of `tier_from_thresholds`.

`inflation_ratio = rendered_text / initial_HTML_text`. A ratio near 1.0 means the content is already in the raw HTML (good); a high ratio means most content only appears after client-side rendering (bad). `_tier_from_inflation` (`render_accessibility.py:25`) returns the **highest** tier whose tolerated max inflation is *not exceeded*:

| Inflation ratio | Tier |
|---|---|
| `≤ 1.5` | 5 |
| `≤ 2.5` | 4 |
| `≤ 4.0` | 3 |
| `≤ 8.0` | 2 |
| `> 8.0` | 1 |

**Hard override:** if `js_only_content` is true (almost nothing in raw HTML, lots after render), the tier is forced to **1** with note "content invisible without JS (js_only_content)" — the content is effectively invisible to non-rendering crawlers regardless of ratio.

**Neutral when render data is missing** (`render_accessibility.py:39`): if the `render_mode` bundle is empty, return tier `_NEUTRAL = 3` with `evidence={"render_data": False}` and note "render mode unavailable — neutral score" — mirroring the `load_speed` non-punitive convention. Always `scored_by = "deterministic"`.

**Evidence:** `js_only_content`, `js_dependent`, `inflation_ratio`, `initial_text_chars`, `rendered_text_chars`.

---

### 6.10 Criterion 10 — Answer Readability (`answer_readability.py`) — *v3*

**Reads:** `bundle["readability"]` (`word_count`, `flesch_reading_ease`, `avg_sentence_length`) and `bundle["chunker"]` (`chunk_count`).
**Config:** `flesch_tiers` = `{1: 0, 2: 20, 3: 30, 4: 45, 5: 55}`, `max_avg_sentence_len: 28`, `min_chunks_for_credit: 3`, `min_word_count: 50` (`scoring.yaml:109-115`).

The intent: answer engines lift concise, well-segmented, quotable passages. Fully deterministic.

**Floor guard** (`answer_readability.py:37`): if `word_count < 50` **or** `flesch_reading_ease is None`, return tier **1** immediately with note "too little readable content" — there is no readable answer to assess.

**Base tier** from Flesch reading ease via `tier_from_thresholds`. The bands sit low on purpose (per YAML comment) because technical content is naturally harder — clarity is rewarded without demanding listicle-level simplicity:

| Flesch reading ease | Base tier |
|---|---|
| `< 20` | 1 |
| `20–<30` | 2 |
| `30–<45` | 3 |
| `45–<55` | 4 |
| `≥55` | 5 |

**Adjustments** (`answer_readability.py:50`), then `clamp_tier`:
- **−1** if `avg_sentence_length > 28` words — long sentences are hard to extract a clean answer from.
- **+1** if `chunk_count >= 3` (page is segmented into several retrieval-friendly passages); **−1** if `chunk_count <= 1` (a monolithic, poorly segmented block). Note: a `chunk_count` of exactly 2 triggers neither adjustment.

**Evidence:** `word_count`, `flesch_reading_ease`, `avg_sentence_length`, `chunk_count`, `base_tier`, and the list of `adjustments` applied.
**Notes:** the adjustment list (e.g. "long sentences (34 words avg); 5 passages") or, if none, "Flesch {n}".

---

### 6.11 Summary table — all ten criteria

| # | Criterion (`module`) | Primary signal(s) read | scored_by | Tier-5 condition | Tier-1 condition |
|---|---|---|---|---|---|
| 1 | Schema Markup (`schema_markup`) | `schema_jsonld.types/block_count/invalid_blocks`; `glossary.opportunity` | deterministic | ≥3 high-value JSON-LD types present | No structured data (`block_count == 0`) |
| 2 | Q&A Blocks (`qa_blocks`) | `qa_blocks.pair_count`; `schema_jsonld.types` (FAQPage) | deterministic | ≥5 real Q&A pairs (FAQPage adds +1 tier) | 0 Q&A pairs |
| 3 | Stats in HTML (`stats_in_html`) | `stats.count/hits/by_kind` (+ LLM disqualifier) | deterministic / hybrid | ≥10 distinct genuine statistics | 0 statistics |
| 4 | Entity Consistency (`entity_consistency`) | `entities.ratio/entity_count/first_person_count` | deterministic | ratio ≥2.5 (or entity named, zero first-person) | Page never names the entity (both counts 0); ratio `<0.5` → tier 2 |
| 5 | Heading Structure (`heading_structure`) | `headings.h23_question_ratio/missing_h1/template_h1` | deterministic | ≥65% question H2/H3, no H1 penalties | `<10%` question headings (and after −1/−1 H1 penalties) |
| 6 | Content Depth (`content_depth`) | `readability.word_count`, `stats.count`, `chunker` text, tone; + LLM | deterministic / hybrid | ≥1500 words (+depth/stats signals); blended with LLM 5 | `<200` words (short + promotional can drop further) |
| 7 | Citation Signals / E-E-A-T (`citation_signals`) | `eeat.author/date`, `links.authority_count`, schema fallback | deterministic | author + date + ≥5 authority links | no author, no date, 0 authority links |
| 8 | Load Speed (`load_speed`) | `pagespeed.performance_score`; `render_mode.js_only_content` | pagespeed / deterministic | PSI mobile perf ≥90 (no JS-only penalty) | PSI `<30` (3 when PSI unavailable; −1 if JS-only) |
| 9 | Render Accessibility (`render_accessibility`) | `render_mode.inflation_ratio/js_only_content` | deterministic | inflation ratio ≤1.5 | `js_only_content` true, or inflation `>8.0` (3 when render data missing) |
| 10 | Answer Readability (`answer_readability`) | `readability.flesch/avg_sentence_length`; `chunker.chunk_count` | deterministic | Flesch ≥55, short sentences, ≥3 passages | `<50` words / no Flesch; or Flesch `<20` |

**Cross-cutting design principles visible across the ten scorers:**
- **Deterministic-first, LLM-optional.** Only criteria 3 and 6 ever call the model, and both degrade gracefully to a deterministic tier if Ollama is absent — a run is never blocked by the LLM. The LLM is a *disqualifier* for stats (can only lower the count) and an *averaging second opinion* for depth (`(det + llm) / 2`).
- **Missing data is neutral, not punitive.** `load_speed` and `render_accessibility` return tier 3 when their external/render data is unavailable, so a page is never penalised for a measurement the pipeline couldn't take.
- **Penalties encode real defects.** Malformed schema (−1), missing/template H1 (−1 each), JS-only content (−1 on load_speed and a hard tier-1 on render_accessibility), and short promotional copy (−1 on depth) are docked because they map to concrete audit findings, not abstract ideals.
- **Config is the single source of truth.** Every threshold, vocabulary, and penalty lives in `config/scoring.yaml`; the in-code dict defaults merely mirror it, so the rubric can be retuned without touching the scorers.

## 7. NLP Layer — LLM Client, Tone, Perplexity

This subsystem holds every place the AEO Crawler talks to a language model or an external answer engine. The unifying design rule, stated in the package docstring and honored by every function below, is **deterministic-first**: the crawler scores 7 of its 8 rubric criteria purely from parsed signals, and the LLM is used only as a *second opinion* on content depth (criterion 6) and to disqualify spurious statistics (criterion 3). Perplexity is used only as the Independent Validator's external citation signal. **No LLM path is a hard dependency** — every method here returns `None` (or a deterministic fallback value) when the provider is disabled, unreachable, unkeyed, or returns garbage, so a down model can never break a scoring or validation run.

The layer is four files:

| File | Role |
|---|---|
| `src/aeo/nlp/__init__.py` | Package docstring stating the deterministic-first contract; `load_prompt()` template loader |
| `src/aeo/nlp/llm.py` | Provider-agnostic `LLMClient` (Ollama vs cloud), JSON extraction, cached singleton |
| `src/aeo/nlp/tone.py` | Deterministic marketing-fluff detector (`analyze()`) — no LLM at all |
| `src/aeo/nlp/perplexity.py` | Injectable `PerplexityClient` + `CitationProbe` for the validator's real-world citation check |

---

### 7.1 Package surface — `src/aeo/nlp/__init__.py`

The module docstring (`__init__.py:1`) is the canonical statement of intent for the whole layer:

> "The crawler is deterministic-first: 7 of the 8 rubric criteria are scored purely from parsed signals. The LLM is used only as a *second opinion* for content depth (criterion 6) and to disqualify spurious statistics (criterion 3). Everything here degrades gracefully — if Ollama is unreachable or disabled, callers get `None` and fall back to deterministic scoring."

Read that as the contract every function in this section must uphold.

#### `load_prompt(name: str) -> str` — `__init__.py:18`

Reads a prompt template from the sibling `nlp/prompts/` directory. `name` is the bare filename *without* extension; the function appends `.txt` and reads it UTF-8.

```python
_PROMPT_DIR = Path(__file__).parent / "prompts"   # __init__.py:15
def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
```

- **Inputs → outputs:** template stem (`str`) → full template text (`str`).
- **Side effects:** one filesystem read of `src/aeo/nlp/prompts/<name>.txt`. No network, no DB.
- **Failure behavior:** does **not** swallow errors — a missing template raises `FileNotFoundError`. This is deliberate: prompts are code-shipped assets, so a missing prompt is a deploy bug, not a runtime degradation case (unlike the network paths below, which all return `None`).

---

### 7.2 LLM client — `src/aeo/nlp/llm.py`

`LLMClient` is a thin **facade** over one of two interchangeable backends, chosen by config. The public surface (`LLMClient`, `generate`, `generate_json`, `enabled`, `provider`, `model`, `get_client`) was kept byte-for-byte compatible with the old Ollama-only client so that every scorer and pipeline call-site keeps working without edits (`llm.py:12`).

**Three deliberate design choices** (from the module docstring, `llm.py:17`):
1. **Synchronous on purpose** — scoring runs in worker threads, not the async crawl loop, and a blocking call keeps scorers trivial to test.
2. **Never raises** — every method returns `None` on failure so a down/misconfigured provider never breaks a scoring run.
3. **Defensive JSON** — `generate_json` asks for JSON output *and* still defends against models that wrap the object in prose, using a 3-strategy extraction.

#### Config knobs it consumes — `LLMCfg`

Every value comes from the `LLMCfg` settings block (imported at `llm.py:36`), populated from environment variables prefixed `AEO__LLM__`:

| `LLMCfg` field | Env var | Used by | Meaning |
|---|---|---|---|
| `provider` | `AEO__LLM__PROVIDER` | `_make_backend`, `LLMClient.provider` | `"ollama"` (default) or `"cloud"` — selects backend |
| `enabled` | `AEO__LLM__ENABLED` | `enabled` gate, constructor | Master on/off switch |
| `model` | `AEO__LLM__MODEL` | Ollama backend, `model` fallback | Local Ollama model name |
| `temperature` | `AEO__LLM__TEMPERATURE` | both backends | Sampling temperature |
| `num_predict` | `AEO__LLM__NUM_PREDICT` | both backends | Max tokens (`options.num_predict` for Ollama; `max_tokens` for cloud) |
| `timeout_sec` | `AEO__LLM__TIMEOUT_SEC` | both backends | `httpx.Client` timeout |
| `host` | `AEO__LLM__HOST` | Ollama backend | Base URL of local Ollama, e.g. `http://localhost:11434` |
| `cloud_model` | `AEO__LLM__CLOUD_MODEL` | cloud backend, `model` | Cloud model id |
| `cloud_base_url` | `AEO__LLM__CLOUD_BASE_URL` | cloud backend | OpenAI-compatible base URL |
| `cloud_api_key` | `AEO__LLM__CLOUD_API_KEY` | cloud backend | Bearer token |

To flip an environment: set `AEO__LLM__PROVIDER=ollama|cloud` and supply `AEO__LLM__CLOUD_API_KEY` for cloud. Ollama is the dev/offline default (free, local); cloud is chosen in production for quality (`llm.py:5`–`15`).

#### The backend protocol — `_Backend` (`llm.py:41`)

A structural `Protocol` requiring a `model: str` attribute and one method:

```python
def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None: ...
```

Two concrete implementations satisfy it. Both share the same failure contract: catch *all* exceptions, log a `llm_generate_failed` warning with provider/model/error, and return `None`.

#### `_OllamaBackend` — `llm.py:47`

Talks to a local Ollama server's `/api/generate` endpoint.

- `__init__(self, cfg: LLMCfg)` stores cfg and exposes `self.model = cfg.model`.
- `generate(prompt, system, *, json_mode)` builds this payload (`llm.py:55`):
  ```python
  {
    "model": cfg.model,
    "prompt": prompt,
    "stream": False,
    "options": {"temperature": cfg.temperature, "num_predict": cfg.num_predict},
  }
  ```
  - If `system` is provided, adds `payload["system"] = system`.
  - If `json_mode`, adds `payload["format"] = "json"` (Ollama's native JSON-grammar constraint).
- **Network call:** `POST {cfg.host}/api/generate`, JSON body, wrapped in an `httpx.Client(timeout=cfg.timeout_sec, transport=sync_transport())`. Returns `resp.json().get("response", "").strip()`.
- **Side effects:** one outbound HTTP POST to the local Ollama host. No DB, no files.
- **Failure:** any exception → `log.warning("llm_generate_failed", provider="ollama", …)` and `return None` (`llm.py:76`).

#### `_CloudBackend` — `llm.py:82`

Talks to any OpenAI-compatible `/chat/completions` endpoint (OpenAI, Gemini's compat endpoint, Together, etc.).

- `__init__` exposes `self.model = cfg.cloud_model`.
- `generate(...)` builds a chat-style request (`llm.py:89`):
  - Messages: optional `{"role":"system",...}` then `{"role":"user","content":prompt}`.
  - Payload `{"model": cfg.cloud_model, "messages": [...], "temperature": cfg.temperature, "max_tokens": cfg.num_predict}`.
  - If `json_mode`, adds `payload["response_format"] = {"type":"json_object"}` (OpenAI JSON mode).
  - Header `Authorization: Bearer {cfg.cloud_api_key or ''}`.
- **Network call:** `POST {cfg.cloud_base_url}/chat/completions`. Extracts `resp.json()["choices"][0]["message"]["content"]`, returns `.strip()` or `None` if content is falsy.
- **Failure:** any exception → `log.warning("llm_generate_failed", provider="cloud", …)` and `return None` (`llm.py:117`).

#### Force-IPv4 transport wiring

Both backends import and use a shared transport: `from ..crawl.transport import sync_transport` (`llm.py:69` and `llm.py:105`), passed as `transport=sync_transport()` into `httpx.Client`. This is the same pinned-transport the crawl layer uses to force IPv4 / control connection behavior, ensuring LLM calls share the project's hardened HTTP stack rather than httpx defaults. The import is **lazy** (done inside `generate`, not at module top) to avoid pulling the crawl layer into import order for callers that never touch the network.

#### `_make_backend(cfg) -> _Backend` — `llm.py:123`

The provider switch:
```python
if cfg.provider == "cloud":
    return _CloudBackend(cfg)
return _OllamaBackend(cfg)     # default / fallthrough
```
Note the default is Ollama for *any* value that isn't exactly `"cloud"` — a typo'd provider degrades to local rather than erroring.

#### `class LLMClient` — `llm.py:129`

The public facade. Constructed with an `LLMCfg`.

- **`__init__(self, cfg)`** (`llm.py:132`): stores cfg and builds the backend **only if enabled** — `self._backend = _make_backend(cfg) if cfg.enabled else None`. When disabled, no backend object exists at all.

- **`enabled -> bool`** (property, `llm.py:136`): `self._cfg.enabled and self._backend is not None`. This is the `.enabled` gate scorers check before bothering to build a prompt — the double condition guards against the (impossible-by-construction but defensive) case of an enabled cfg with a `None` backend.

- **`provider -> str`** (`llm.py:140`): returns `cfg.provider`.

- **`model -> str`** (`llm.py:144`): returns `self._backend.model` if a backend exists, else falls back to `cfg.model`. So even when disabled, `model` reports the configured local model name rather than crashing.

- **`generate(prompt, system=None, *, json_mode=False) -> str | None`** (`llm.py:148`): the core text call. **Re-checks the gate at call time**: `if backend is None or not self._cfg.enabled: return None`. This means flipping `enabled` to false (or having no backend) yields `None` *before* any network attempt — the deterministic fallback path. Otherwise delegates to `backend.generate(...)`.

- **`generate_json(prompt, system=None) -> dict | None`** (`llm.py:154`): calls `self.generate(..., json_mode=True)`; if the raw result is falsy returns `None`; otherwise runs it through `_extract_json`. So callers get either a parsed `dict` or `None` — never a raw string they'd have to parse themselves.

#### `_extract_json(text) -> dict | None` — `llm.py:161`

The 3-strategy defensive JSON extractor — the reason `generate_json` survives models that ignore JSON-mode and wrap output in prose, markdown fences, or chatter. It escalates:

1. **Whole-string parse** (`llm.py:164`): `json.loads(text)`; accept only if the result is a `dict`. (Guards against the model returning a bare list or scalar.)
2. **First `{...}` regex span** (`llm.py:172`): `re.search(r"\{.*\}", text, re.DOTALL)` — greedy, so it spans from the first `{` to the last `}`. Parse that substring; accept if `dict`.
3. **Balanced-brace scan** (`llm.py:182`): walks the string char by char tracking `{`/`}` depth; when depth returns to 0 it tries to parse that exact balanced span. This correctly handles trailing prose after the object and nested objects where the greedy regex might over- or mis-capture. On a parse failure it resets `start = -1` and keeps scanning for the next candidate object.

Returns the first `dict` any strategy yields, else `None`. Only `dict` objects are ever returned — never lists or scalars.

#### `get_client() -> LLMClient` — `llm.py:201`

`@lru_cache(maxsize=1)` factory: builds one `LLMClient` from `get_settings().llm` and memoizes it process-wide. So the whole process shares a single configured client (cheap, and config is read once). Because the backend is built eagerly in the constructor based on `enabled`, toggling settings after first call won't rebuild it within a process.

---

### 7.3 Tone / marketing-fluff detection — `src/aeo/nlp/tone.py`

This file is **pure deterministic NLP — no LLM, no network, no I/O.** Promotional density feeds content depth (criterion 6): a page padded with "industry-leading, best-in-class, cutting-edge" boilerplate reads thin to an answer engine regardless of length. The docstring is explicit that this is intentionally a keyword/phrase heuristic — "cheap, explainable, and good enough to flag the worst offenders without an LLM call" (`tone.py:1`). It is the concrete embodiment of the deterministic-first principle: rather than ask the LLM "is this fluffy?", the system answers it with a vocabulary and two thresholds.

#### The vocabulary — `MARKETING_PHRASES` (`tone.py:20`)

A 50-entry tuple of lower-cased promotional phrases, matched case-insensitively as whole phrases. Hyphenated and spaced variants are both listed so either spelling is caught. The full list:

```
industry-leading, industry leading, best-in-class, best in class,
cutting-edge, cutting edge, world-class, world class,
state-of-the-art, state of the art, next-generation, next generation,
seamless, seamlessly, robust, powerful, innovative, innovation,
revolutionary, game-changing, game changer, leverage, synergy,
empower, empowering, unlock, unleash, transformative,
trusted by, end-to-end, turnkey, one-stop, holistic, bespoke,
mission-critical, unparalleled, unmatched, premier,
leading provider, comprehensive suite, drive value, value-add,
best in the industry, thought leader, thought leadership
```

`_PATTERNS` (`tone.py:33`) precompiles each phrase into a word-boundary regex `\b{escaped phrase}\b` with `re.IGNORECASE`. Compiling once at import keeps `analyze()` cheap when called per page. `re.escape` neutralizes regex-special characters (e.g. the hyphens) so phrases match literally.

#### The thresholds (`tone.py:35`)

A page is flagged promotional if it crosses **either** bar (OR, not AND):

| Constant | Value | Meaning |
|---|---|---|
| `_MIN_HITS` | `5` | Absolute count: ≥ 5 total marketing-phrase occurrences |
| `_MIN_DENSITY_PER_1K` | `6.0` | Rate: ≥ 6 occurrences per 1,000 words |

The two-bar design catches both short pages that are wall-to-wall fluff (the density bar trips first) and long pages that accumulate many buzzwords without being dense (the absolute-count bar trips first).

#### `analyze(text: str) -> dict[str, Any]` — `tone.py:40`

Counts marketing phrases in `text` and reports density.

Algorithm:
1. `wc = word_count(text)` (from `..utils.text`).
2. For each compiled pattern, `n = len(pat.findall(text))`; if `n`, record `found[phrase] = n` and add to `total`.
3. `density = total / wc * 1000` if `wc` else `0.0` (the `if wc` guards division by zero on empty text).
4. `is_promotional = total >= 5 or density >= 6.0`.

Returns a dict:

| Key | Type | Meaning |
|---|---|---|
| `marketing_phrases` | `list[str]` | sorted list of the distinct phrases that appeared |
| `marketing_hits` | `dict[str,int]` | phrase → occurrence count |
| `marketing_count` | `int` | total occurrences across all phrases (`total`) |
| `word_count` | `int` | word count of the input |
| `promotional_density_per_1k` | `float` | occurrences per 1,000 words, rounded to 2 dp |
| `is_promotional` | `bool` | whether either threshold was crossed |

- **Inputs → outputs:** page text → the dict above.
- **Side effects:** none. No DB, no network, no files. Fully deterministic and idempotent.

---

### 7.4 Perplexity client — `src/aeo/nlp/perplexity.py`

This is the **Independent Validator's real-world citation signal**. It asks Perplexity a page's target question and reports whether the page's own domain shows up in the answer's citations. The docstring frames this as the v4 fix for *circular validation* (`perplexity.py:1`): rather than re-scoring a page against the very rubric the recommender optimized for, the validator checks an **external** signal the system does not control — does a real answer engine actually cite this page?

It deliberately **mirrors `LLMClient`**: an OpenAI-compatible `/chat/completions` POST, an `enabled` gate that fronts everything, the same shared `sync_transport()`, and the same "return `None` on any failure, never raise" contract. A down or unkeyed Perplexity must never break validation; the validator then relies on its deterministic checks alone (`perplexity.py:9`).

#### Config knobs — `PerplexityCfg`

Consumed via `get_settings().perplexity` (imported `perplexity.py:28`):

| Field | Used by | Meaning |
|---|---|---|
| `enabled` | `enabled` gate | Master switch |
| `api_key` | `enabled` gate + Bearer header | Perplexity API key; **also gates `enabled`** (no key ⇒ disabled) |
| `model` | request payload, `model` property | Perplexity model id |
| `base_url` | request URL | API base; `.rstrip('/')` applied before appending `/chat/completions` |
| `timeout_sec` | `httpx.Client` timeout | request timeout |

#### `@dataclass(slots=True) CitationProbe` — `perplexity.py:33`

The structured outcome of one citation query. `slots=True` for a compact, attribute-fixed record:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `question` | `str` | — | The question that was asked |
| `cited` | `bool` | — | Whether the target domain was cited (structured **or** in-text) |
| `citations` | `list[str]` | `[]` | All citation strings Perplexity returned |
| `matched` | `list[str]` | `[]` | The subset of `citations` containing our domain |
| `answer` | `str` | `""` | The raw answer text |

#### `_domain(url) -> str` — `perplexity.py:44`

Normalizes a URL to a bare host for matching: lower-cases `urlsplit(url).netloc` (falling back to the raw string if there is no netloc), then strips a leading `www.`. This makes the domain comparison robust to scheme, path, and `www` differences.

#### `class PerplexityClient` — `perplexity.py:49`

Thin facade over Perplexity's chat-completions API. Injectable — constructed with a `PerplexityCfg`, so tests can pass a fake config or the whole client can be swapped at the call-site (hence "injectable" in the design).

- **`__init__(self, cfg: PerplexityCfg)`** (`perplexity.py:53`): stores cfg only. No backend object, no network — construction is free.

- **`enabled -> bool`** (`perplexity.py:56`): `self._cfg.enabled and bool(self._cfg.api_key)`. Note the **double gate**: even with `enabled=True`, a missing API key reports disabled. This prevents firing keyless requests that would 401.

- **`model -> str`** (`perplexity.py:61`): returns `cfg.model`.

- **`cited(self, question, *, target_url) -> CitationProbe | None`** (`perplexity.py:64`): the core probe.
  - **Early-out / fallback gate:** `if not self.enabled or not question.strip(): return None` — disabled, unkeyed, or empty-question ⇒ `None` before any network call.
  - Lazily imports `sync_transport` (same force-IPv4 transport as the LLM client).
  - **Request:** `POST {cfg.base_url.rstrip('/')}/chat/completions` with payload `{"model": cfg.model, "messages": [{"role":"user","content": question}]}` and header `Authorization: Bearer {cfg.api_key or ''}`, inside `httpx.Client(timeout=cfg.timeout_sec, transport=sync_transport())`.
  - **Failure:** any exception → `log.warning("perplexity_query_failed", model=..., error=...)` and `return None` (`perplexity.py:87`). The docstring stresses the contract: returning `None` lets the validator **distinguish "not run" from a real "not cited" result** — a crucial three-state distinction (None / cited=False / cited=True), not a boolean.
  - On success, hands the parsed JSON to `_probe`.

- **`_probe(question, target_url, data) -> CitationProbe`** (static, `perplexity.py:93`): the **defensive parser**, built to tolerate response-shape drift:
  1. **Citations:** `citations = [str(c) for c in (data.get("citations") or []) if c]` — handles missing key, `None`, and falsy entries; coerces each to `str`.
  2. **Answer:** `answer = str(data["choices"][0]["message"]["content"] or "")` inside a `try` catching `(KeyError, IndexError, TypeError)`; on any of those it sets `answer = ""`. So a malformed `choices` structure degrades to empty text rather than crashing.
  3. **Domain match:** `domain = _domain(target_url)`; `matched = [c for c in citations if domain and domain in c.lower()]`.
  4. **`cited` decision (two-tier):** `cited = bool(matched) or (bool(domain) and domain in answer.lower())`. First it trusts structured citations; **if none matched, it falls back to scanning the answer text** for the domain. This is the response-shape-drift tolerance: even if Perplexity stops returning a top-level `citations` array, an in-text mention of the domain still counts as cited.
  5. Returns a fully populated `CitationProbe`.

  Note `_probe` itself never raises and always returns a `CitationProbe` — all the `None`-returning failure handling lives in `cited()`'s transport try/except.

#### `get_perplexity_client() -> PerplexityClient` — `perplexity.py:115`

`@lru_cache(maxsize=1)` factory mirroring `get_client()`: builds one client from `get_settings().perplexity` and memoizes it process-wide.

---

### 7.5 End-to-end: how the deterministic-first guarantee holds

Tracing every LLM/network path in this layer, each has a guaranteed non-network fallback:

- **`LLMClient.generate` / `generate_json`** — gated twice (constructor builds no backend when disabled; `generate` re-checks `enabled` at call time) and wrapped in catch-all try/except in each backend. Scorers receive `None` ⇒ they fall back to deterministic scoring for criteria 6 and 3.
- **`tone.analyze`** — never touches an LLM at all; it *is* the deterministic substitute for "is this page fluffy?", using a fixed 50-phrase vocabulary and the `≥5 hits OR ≥6/1k` thresholds.
- **`PerplexityClient.cited`** — gated on `enabled && api_key && non-empty question`, transport wrapped in try/except, and the parser is defensive on every field. The validator gets `None` ⇒ it relies on its deterministic checks alone, and it can tell "not run" (`None`) apart from "ran, not cited" (`cited=False`).
- **`load_prompt`** is the one intentional exception that *does* raise — prompts are shipped code assets, so their absence is a deploy error, not a runtime degradation to absorb.

Shared infrastructure across the networked paths: both `LLMClient` backends and `PerplexityClient` route through `crawl.transport.sync_transport()` (force-IPv4 / hardened httpx transport), use `httpx.Client(timeout=cfg.timeout_sec, ...)`, log structured warnings via `..logging.get_logger`, and are exposed as `@lru_cache(maxsize=1)` process singletons (`get_client`, `get_perplexity_client`).

## 8. Processor & Reference Layer — Gap Analysis, Blueprint, Generator, Coverage, Feedback

This is the v4 "brain": the layer that turns raw per-page rubric scores and the crawled sitemap into *prioritized, actionable* analysis. It is built from two packages:

- **`aeo.processor`** — the consumers. Two deterministic analyses: the **Dual-Layer Gap Analysis** (is *this page* up to code?) and the **Coverage Diff** (which *pages* are missing from the site?).
- **`aeo.reference`** — the standards the processor measures against. A three-layer (L1/L2/L3) **Reference Architecture** that produces a versioned **Blueprint** (the ideal site), plus best-practice targets, a query-intent classifier, and a human-gated **validated-wins feedback loop** that lets cited pages nudge the targets over time.

The whole layer follows the codebase's **deterministic-first** principle: every output is fully reproducible with no LLM in the loop; the model only *upgrades* quality (extra seed questions, extra supporting pages), and it is never a hard dependency. Closed-vocabulary Pydantic `Literal`s keep a hallucinating model from inventing categories the rest of the system can't route.

---

### 8.1 Package surface — what each `__init__` exports

#### `processor/__init__.py` (processor/__init__.py:1)
Re-exports the processor's public API. From `coverage_diff`: `CoverageDiffResult`, `DiscoveredPage`, `MissingNode`, `ThinCluster`, `coverage_diff`. From `gap_analysis`: `CompetitorPage`, `CriterionGap`, `GapResult`, `analyze_gap`, `persist_gap`, `select_competitor`. The docstring states its role plainly: it "turns raw scores into actionable analysis" and "feeds the Recommender."

#### `reference/__init__.py` (reference/__init__.py:1)
The single import surface for the Reference Layer. Re-exports:
- Blueprint contract: `Blueprint`, `CoverageCluster`, `CoverageMap`, `SitemapNode`, `normalize_slug`, `GENERATOR_DETERMINISTIC`.
- Feedback: `CitationObservation`, `CriteriaRefinement`, `propose_criteria_refinements`.
- Best-practice loader: `Reference`, `PageArchitecture`, `load_reference`, `DEFAULT_TARGET`.
- Query intent: `QueryIntentCfg`, `classify_intent`.

The docstring marks it "(provisional)" and notes "Consumers depend only on these accessors" — a deliberate seam so a richer future implementation (vector-backed, per-vertical) only changes the loaders, not their consumers.

---

### 8.2 Dual-Layer Gap Analysis — `processor/gap_analysis.py`

This is the Processor's deterministic core. It converts one page's 10-criterion `PageScore` into a prioritized deficiency list by measuring **two** normalized gaps and blending them.

#### The algorithm and the WHY

Two gaps, fixed blend weights (`gap_analysis.py:30-31`):

| Constant | Value | Meaning |
|---|---|---|
| `_BESTPRACTICE_WEIGHT` | `0.6` | weight of the gap vs. the Reference Layer targets |
| `_COMPETITOR_WEIGHT` | `0.4` | weight of the gap vs. the best competitor page |
| `_ROUND` | `3` | decimal places all normalized gaps round to |

- **Best-practice gap (60%)** — per criterion, the shortfall `max(0, target - actual)` in tier points, weighted by the rubric weight, summed, and normalized to `[0,1]`. The normalizer is the *maximum possible* weighted shortfall, `weight * max(0, target - scale_min)`, so `1.0` means "every criterion sits at the rubric floor relative to its target."
- **Competitor gap (40%)** — exactly the same shortfall, but measured against the *best competitor page for this page's query intent* (`max(0, competitor_tier - actual)`), normalized against `weight * max(0, competitor_tier - scale_min)`.

The design intent (docstring, `gap_analysis.py:1-18`): when **no competitor exists for the intent**, the competitor layer is *absent* (`competitor_gap is None`) and `overall_gap` falls back to the best-practice gap alone — "a page is never flattered by missing competitor data." The ordered `criterion_gaps` list (largest weighted deficiency first) is what the Recommender consumes. Pure and deterministic; persistence is a separate thin helper.

#### Dataclasses

`CompetitorPage` (`gap_analysis.py:35`) — a scored competitor as the gap analysis consumes it: `page_id`, `intent`, `total`, and `tiers: Mapping[str, int]` (criterion name → that competitor's 1-5 tier).

`CriterionGap` (`gap_analysis.py:46`) — one row of the prioritized list:
```
criterion, actual, target, bestpractice_gap, competitor (tier or None),
competitor_gap, weight, priority   # priority = weight * blended gap — the ordering key
```

`GapResult` (`gap_analysis.py:60`) — the whole result: `page_id`, `run_id`, normalized `bestpractice_gap`, `competitor_gap` (`float | None`), `overall_gap`, the `criterion_gaps` list, `competitor_page_id`, `intent`. `to_detail()` (`gap_analysis.py:71`) builds the JSONB payload for the `gap_analyses.detail` column, including a `competitor_available` boolean and the two weights, plus every `CriterionGap` as a dict.

#### Functions

**`select_competitor(candidates, intent) -> CompetitorPage | None`** (`gap_analysis.py:85`)
The best competitor for an intent = the **highest-`total`** candidate whose `intent` matches. Returns `None` when no candidate shares the intent (or `intent is None`), which *drops* the competitor layer rather than comparing across mismatched intents. Inputs: a sequence of `CompetitorPage` + an intent string. Pure.

**`_weight_for(rubric, name) -> float`** (`gap_analysis.py:97`)
Internal: the rubric weight for a criterion, or `1.0` if the criterion isn't in the rubric.

**`analyze_gap(score, *, reference=None, rubric=None, competitor=None, intent=None) -> GapResult`** (`gap_analysis.py:101`)
The main entry point. Inputs → output: a `PageScore` (plus optional injected `Reference`, `Rubric`, `CompetitorPage`, and intent string) → a `GapResult`. Defaults lazily call `load_reference()` and `load_rubric()`. Per-criterion logic (`gap_analysis.py:119-153`):
1. `target = reference.target_for(name)`; `weight = _weight_for(...)`.
2. `bp_pts = max(0, target - actual)`; accumulate `bp_weighted` and the `bp_max` normalizer.
3. If a competitor tier exists, `comp_pts = max(0, comp_tier - actual)`; accumulate `comp_weighted`/`comp_max`.
4. **Per-criterion blend:** with a competitor, `blended = 0.6*bp_pts + 0.4*comp_pts`; without, `blended = bp_pts`. `priority = weight * blended`.
5. A row is emitted only when `bp_pts > 0 or comp_pts > 0` (clean criteria are omitted).

Rows are sorted by `(-priority, criterion)` (`gap_analysis.py:155`) — largest weighted deficiency first, name as a stable tiebreaker. Then the two normalized gaps are computed (guarding divide-by-zero), and `overall` is either the best-practice gap alone (no competitor) or the 0.6/0.4 blend of the two normalized gaps. **Side effects: none** — pure function.

**`persist_gap(result) -> int`** (`gap_analysis.py:180`)
The only DB-touching function. Lazily imports `storage.repos.gaps` and writes to the **`gap_analyses`** table via `gaps_repo.put(...)`. Key detail: `competitor_gap` is stored as **`0.0` when absent** (the column is `NOT NULL`); the `detail.competitor_available` flag preserves the distinction so a consumer can tell "matched the competitor" from "no competitor existed." Returns the row id.

---

### 8.3 Coverage Diff — `processor/coverage_diff.py`

The v4 *site-level* gap — a new kind of gap. Where the Dual-Layer Gap Analysis asks "is this room up to code?", the Coverage Diff asks "which rooms are missing from the house?" It compares the client's **discovered, classified sitemap** against the blueprint's **ideal sitemap** and emits: pages **missing entirely** (net-new content targets) and topical **clusters too thin** to earn authority (below the blueprint's `min_pages`). Pure — no I/O; the caller supplies discovered pages and a `Blueprint`.

#### Matching: deterministic and explainable

Two tunable constants (`coverage_diff.py:26-32`):

| Constant | Value | Meaning |
|---|---|---|
| `_STOPWORDS` | `{what, is, are, the, a, an, to, vs, of, and, or, how, why, for, in, on, with, your, you, guide, page}` | slug tokens with no topical signal, excluded from overlap matching |
| `_OVERLAP_THRESHOLD` | `0.6` | a discovered slug covers a node when it shares ≥60% of the node's *distinctive* tokens **and** the page-types match |

`_covers(node, page)` (`coverage_diff.py:140`) implements the rule:
1. **Exact normalized slug match always wins** (`page.slug == node.slug`).
2. Otherwise compute the node's distinctive tokens (`_tokens`, `coverage_diff.py:135` — normalize, split on `-`, drop stopwords). No tokens → not covered.
3. **Page-type must match** (`page.page_type != node.page_type` → not covered). This is the guard that lets `/what-is-ctem` be satisfied by `/guides/ctem` while a *blog post never silently satisfies a product page*.
4. Overlap = `|node_tokens ∩ page_tokens| / |node_tokens| ≥ 0.6`.

#### Dataclasses

`DiscoveredPage` (`coverage_diff.py:35`) — `url`, `slug`, `page_type`, `intent="informational"`. The `from_url(url, page_type, intent)` classmethod normalizes the slug via `normalize_slug`. Slug + classification come from Site Discovery + the prioritizer's classifier.

`MissingNode` (`coverage_diff.py:49`) — a blueprint node with no covering client page: `slug, title, page_type, intent, journey_stage, cluster, priority, required_entities, seed_questions, rationale`. `from_node(node)` (`coverage_diff.py:64`) copies a `SitemapNode`'s fields and synthesizes a default rationale (`"Missing {page_type} for the {cluster or 'topic'} cluster"`) when the node carries none. These become the **net-new content recommendations**.

`ThinCluster` (`coverage_diff.py:74`) — `name, present_count, min_pages, missing_slugs`; the `shortfall` property = `max(0, min_pages - present_count)`.

`CoverageDiffResult` (`coverage_diff.py:88`) — `topic, blueprint_version, total_nodes, matched (slugs), missing (MissingNode list), thin_clusters`. Derived: `matched_count`, `coverage_pct` (= `matched/total*100`, rounded to 1 dp, `0.0` when no nodes), and `missing_by_priority()` which sorts missing nodes by `(-priority, slug)` — **the build order**. `to_detail()` / `from_detail()` (`coverage_diff.py:109`, `:121`) round-trip through the `coverage_diffs.detail` JSONB column (the site report reads the diff back from the DB).

#### Function

**`coverage_diff(blueprint, discovered) -> CoverageDiffResult`** (`coverage_diff.py:153`)
For each `blueprint.sitemap` node, if **any** discovered page `_covers` it, add to `matched`/`covered_slugs`; else add a `MissingNode`. Then for each `blueprint.coverage.clusters` cluster, count how many of its slugs are covered; if `present < cluster.min_pages`, emit a `ThinCluster` listing the uncovered slugs. **Side effects: none.**

---

### 8.4 The Blueprint contract — `reference/blueprint.py`

This module **is the contract** — the single typed surface every downstream block depends on (the generator produces it; the Coverage Diff reads its `sitemap`; the recommender turns missing nodes into recs; `storage.repos.blueprints` round-trips it through a JSONB column). Pure: Pydantic models + a deterministic hash, no I/O.

#### Two product-load-bearing ideas

1. **Versioning (reuse-vs-bump).** Regenerating the blueprint every run would "move the measuring stick" and make week-over-week scores meaningless. So a blueprint carries a monotonic `version` (assigned by the repo) and a `content_hash` over its *inputs*. Identical inputs ⇒ identical hash ⇒ reuse the pinned version; changed inputs ⇒ new version, flagged in the report so a score jump reads as "new baseline."
2. **Guardrailed vocabulary.** `page_type` / `intent` / `journey_stage` are **closed `Literal` sets** aligned with the prioritizer and query-intent classifier, so a hallucinating generator can't invent categories the rest of the system can't route; validation *rejects* out-of-vocab values.

#### Closed vocabularies (`blueprint.py:41-47`)

```python
PageType     = Literal["homepage","product","solution","pillar","blog","about","contact","utility","default"]
Intent       = Literal["commercial","navigational","informational"]
JourneyStage = Literal["awareness","consideration","decision"]
```
`PageType` mirrors `config/prioritization.yaml` base_weights + the `default` fallback; `Intent` mirrors the query-intent classifier (precedence commercial > navigational > informational); `JourneyStage` is the v4 coverage-map dimension. `GENERATOR_DETERMINISTIC = "deterministic"` (`blueprint.py:50`) is the provenance stamp.

#### `normalize_slug(slug) -> str` (`blueprint.py:53`)
The one canonical-path normalizer **both** the blueprint and the Coverage Diff call (matching needs both sides in the same shape). Rule: lowercase, single leading slash, no trailing slash. Tolerates a full URL (keeps only its path via `urlsplit`). Examples: `/What-Is-CTEM/` → `/what-is-ctem`; bare `home` → `/home`; `/` and `""` → `/` (the homepage).

#### Pydantic models

All models use `ConfigDict(extra="forbid")` — unknown keys are rejected.

**`SitemapNode`** (`blueprint.py:71`) — one ideal page. Fields: `slug`, `title`, `page_type` (default `"default"`), `intent` (default `"informational"`), `journey_stage` (default `"awareness"`), `required_entities`, `seed_questions`, `cluster: str | None`, `priority: float = 0.5` (blueprint-importance 0..1, drives missing-page ordering), `rationale`. Validators: `_slug` normalizes via `normalize_slug`; `_clamp_priority` clamps to `[0,1]`; `_clean_list` dedupes-preserving-order and drops blanks from `required_entities`/`seed_questions` (keeps hashes/diffs stable).

**`CoverageCluster`** (`blueprint.py:115`) — one pillar + its supporting pages. `name`, `pillar_slug`, `supporting_slugs`, `min_pages: int = 1`. `min_pages` encodes the v4 "10-20 pieces per cluster" target the Coverage Diff flags *thin* against. Validators normalize the pillar/supporting slugs and force `min_pages ≥ 1`. `slugs()` returns every slug (pillar first), deduped and order-stable.

**`CoverageMap`** (`blueprint.py:153`) — topic-level targets: `required_entities`, `journey_stages`, `clusters`.

**`Blueprint`** (`blueprint.py:164`) — the versioned ideal site for one topic. Fields: `topic`, `version: int = 1`, `generator` (default `"deterministic"`; e.g. `"gemini:<model>"`), `framework_version: str = "0"`, `competitors: list[str]` (domains feeding L1), `sitemap`, `coverage`, `content_hash`, `notes`. Validators: `_topic` rejects empty; `_unique_slugs` (a `model_validator`) rejects duplicate sitemap slugs.
- Accessors: `node_for_slug(slug)`, `slugs()`, `all_required_entities()` (order-stable union of the coverage map's entities and every node's).
- **`hash_inputs()`** (`blueprint.py:221`) — the versioning core. Deterministic SHA-256 over a sorted JSON payload of `topic`, `framework_version`, sorted `competitors`, the sitemap as sorted `(slug, page_type, intent, journey_stage, sorted entities)` tuples, sorted coverage entities, and sorted cluster tuples `(name, pillar_slug, sorted supporting, min_pages)`. **Excludes `version`, `content_hash`, `notes`, and `generator`** — those are provenance, not identity. So a deterministic run and an LLM-augmented run with the *same structure* collapse to one version.
- `with_hash()` returns a copy with `content_hash` set. `to_jsonb()/to_json()/from_jsonb()/from_json()` handle the `blueprints.body` JSONB round-trip.

---

### 8.5 Framework loader (L2) — `reference/framework.py` + `config/framework.yaml`

L2 is the **guardrail + ceiling**: a curated topic taxonomy plus per-criterion definitions, handed to the LLM so synthesis can *enrich but not invent*. Every framework node is already a validated `SitemapNode`, so an out-of-vocab page-type/intent fails *here, at load*, not downstream. Config-over-code, mirroring the rubric/prioritization loaders, cached for the process lifetime.

#### Base priority by page-type (`framework.py:25-35`)
Used when no competitor signal refines a node's importance:

| page_type | base priority | | page_type | base priority |
|---|---|---|---|---|
| pillar | 0.9 | | blog | 0.6 |
| product | 0.85 | | about | 0.4 |
| solution | 0.8 | | contact | 0.4 |
| homepage | 0.7 | | utility | 0.2 |
| | | | default | 0.5 |

"Pillars/products are the AEO cornerstone; utility pages barely matter."

#### Dataclasses
- `CriteriaDefinition` (`framework.py:38`) — the "ceiling" half of L2: `criterion, target, perfect, average, checkable, schema_org`.
- `ClusterDef` (`framework.py:50`) — `name, min_pages, pillar_slug, node_slugs` (pillar first); `supporting_slugs` property = node_slugs minus the pillar.
- `Framework` (`framework.py:62`) — `version, topic, required_entities, journey_stages, nodes, clusters, criteria`; accessors `criteria_definition(name)` and `node_for_slug(slug)`.

#### Functions
- `_node_from_cfg(raw, *, cluster, allowed_entities)` (`framework.py:84`) — builds a validated `SitemapNode` from a YAML entry, **dropping any required-entity outside the topic vocabulary** (the guardrail), and uses `SitemapNode.model_validate` so the closed-vocab Literals raise on invalid `page_type`/`intent`. Priority is set from `_base_priority(page_type)`.
- **`load_framework() -> Framework`** (`framework.py:107`, `@lru_cache(maxsize=1)`) — reads `framework.yaml` via `settings.load_yaml_file`. Builds the allowed-entity set, then walks `clusters` (pillar + supporting → `ClusterDef` + nodes) and `standalone_nodes`, then `criteria_definitions`. **Side effects:** reads the YAML file once, caches for the process.

#### `config/framework.yaml` summary
Seeded for **ONE topic — PEV (Proactive Exposure / Vulnerability management)** on Securin, per the v4 "build for ONE topic end to end before generalizing" sequence. Generalizing = add another topic block, no code change. `version: "1"`, `topic: PEV`.

- **`required_entities`** (the closed entity vocabulary a node may reference): `MITRE ATT&CK, CVSS, EPSS, KEV, CTEM, BAS (Breach & Attack Simulation), RemOps (Remediation Operations), CISA, NIST, Attack Surface`.
- **`journey_stages`**: `[awareness, consideration, decision]`.
- **Clusters** (each with a `min_pages` thin-threshold):

| cluster | min_pages | pillar slug | supporting slugs |
|---|---|---|---|
| `ctem` | 10 | `/what-is-ctem` | `/ctem-vs-vulnerability-management`, `/ctem-program`, `/ctem-tools` |
| `continuous-validation` | 10 | `/what-is-continuous-validation` | `/breach-and-attack-simulation`, `/security-control-validation` |
| `exposure-management` | 10 | `/what-is-exposure-management` | `/attack-surface-management`, `/kev-epss-prioritization` |
| `remediation` | 8 | `/what-is-remediation-operations` | `/vulnerability-remediation-best-practices` |

Each node carries `title, page_type, intent, journey_stage, required_entities, seed_questions`. (Note the `min_pages` of 8-10 vs. only 1-3 authored nodes means *every* PEV cluster is currently flagged thin until more pages are crawled/built — that is the intended "10-20 per cluster authority" target.)
- **`standalone_nodes`**: `/` (homepage/navigational), `/platform` (product/commercial), `/contact` (contact/commercial).
- **`criteria_definitions`** — the ceiling for 10 criteria, each with `target`, a `perfect` vs. `average` description, 3-4 `checkable` items, and a `schema_org` mapping. Targets: `render_accessibility: 5` (the only 5 — "the answer is in the server-rendered HTML; no JS-only content," because JS-only content is invisible to answer engines); all others (`schema_markup, qa_blocks, stats_in_html, entity_consistency, heading_structure, content_depth, citation_signals, load_speed, answer_readability`) target `4`. Example checkables: `qa_blocks` wants "≥3 Q&A pairs"; `stats_in_html` wants "≥6 distinct numeric claims" in text not images; `heading_structure` wants "≥45% question-phrased H2/H3"; `answer_readability` wants a "Lead answer ≤ 50 words" and "Avg sentence ≤ 28 words."

---

### 8.6 Competitor patterns (L1) — `reference/competitor_patterns.py`

L1 is the **empirical floor**: pure aggregation over competitor pages the crawler already extracted — what page-types they publish, which JSON-LD types they use, which topic entities they cover, how long their pages run, and which question-shaped headings recur. It keeps the blueprint grounded in *what Pentera/Cymulate/Picus actually do* rather than a theoretical ideal. No I/O: it reads `(url, ExtractionBundle)` pairs the caller loads from the DB. Every section access is defensive — a partial bundle "must never raise into the generator."

`_MAX_QUESTION_HEADINGS = 25` (`competitor_patterns.py:25`) caps headings so a pathological site can't dominate the prompt/patterns.

**`CompetitorPatterns`** (`competitor_patterns.py:28`) — aggregated signals: `domains`, `page_count`, `page_type_counts`, `schema_type_counts`, `entity_coverage` (entity → # pages mentioning it), `avg_word_count_by_type`, `common_question_headings`. Methods:
- `page_type_share(page_type)` — fraction of competitor pages of this type (the empirical-floor weight); `0.0` if no pages.
- `covered_entities(min_pages=1)` — entities covered by ≥`min_pages` pages, most-covered first.
- `to_summary()` — compact dict for the synthesis prompt / blueprint notes: domains, page_count, page_type_counts, top 8 schema types, covered entities, top 10 question headings.

Helpers: `_searchable_text(bundle)` (`competitor_patterns.py:62`) joins title + h1/h2/h3 + chunk text, lowercased, for entity-mention detection; `_question_headings(bundle)` (`competitor_patterns.py:78`) pulls h2/h3 headings flagged `is_question` from the heading sequence.

**`extract_patterns(pages, *, allowed_entities, domains=None, cfg=None) -> CompetitorPatterns`** (`competitor_patterns.py:89`)
Aggregates across `(url, bundle)` pairs: classifies each URL's page-type via `crawl.prioritize.classify` (loading `PrioritizationCfg` if not injected), tallies schema types, records per-type word counts (averaged at the end, rounded to 1 dp), counts entity mentions (case-insensitive substring against the lowercased searchable text, mapped back to the canonical entity casing), and counts question headings. **Side effects:** none beyond reading the supplied bundles + loading prioritization config.

---

### 8.7 Reference Architecture Generator (L3) — `reference/generator.py`

The headline v4 block: combines L1 (competitor patterns) + L2 (framework) [+ L3 (LLM)] into a versioned `Blueprint`. **Two-track by design.**

Bounds/guardrails (`generator.py:47-51`):

| Constant | Value | Purpose |
|---|---|---|
| `_MAX_AUGMENT_NODES` | `12` | cap on LLM net-new supporting pages (can't balloon the sitemap) |
| `_PRIORITY_FLOOR_BUMP` | `0.3` | max lift a fully-competitor-covered page-type adds to a node's priority |
| `_MAX_QUESTIONS_PER_SLUG` | `5` | cap on LLM seed-question enrichment per node |
| `_MAX_QUESTION_LEN` | `300` | per-question char cap (untrusted/prompt-injectable input) |

`_SYNTH_SYSTEM` (`generator.py:53`) is the system prompt: "You enrich an existing blueprint; you never invent page types, intents, or entities outside the allowed lists. Reply with JSON only."

#### Track 1 — Deterministic floor (always)
- `_refine_priority(node, patterns)` (`generator.py:60`) — lifts a node's base priority by `0.3 * patterns.page_type_share(page_type)` (clamped to 1.0, rounded to 3 dp). No patterns → unchanged.
- `_coverage_map(framework)` (`generator.py:69`) — projects the framework's clusters/entities/stages into a `CoverageMap`.
- `_deterministic_blueprint(topic, framework, patterns)` (`generator.py:86`) — the **framework nodes *are* the base blueprint**, each with its priority refined by competitor share; `generator="deterministic"`; competitor domains + count recorded in `notes`. This path needs no LLM and is fully reproducible.

#### Track 2 — LLM augmentation (optional, bounded, guardrailed)
`_augment_with_llm(base, framework, patterns, llm)` (`generator.py:110`) asks the LLM for two things, then **re-validates every proposal against the contract**; returns `base` unchanged on any failure (the `llm.generate_json` call is wrapped in try/except that logs `blueprint_synthesis_failed`).
1. **Extra seed questions** for existing nodes (`generator.py:140-154`): mapping of slug → questions. Bounded by `[:len(nodes)]` slugs, `_MAX_QUESTIONS_PER_SLUG`, `_MAX_QUESTION_LEN`; rebuilt via `SitemapNode.model_validate` so the contract's dedupe/blank-strip validator runs (`model_copy(update=)` would skip it). Only counts as a contribution if it adds *net-new* questions after dedupe.
2. **Net-new supporting nodes** (`generator.py:156-185`): up to `_MAX_AUGMENT_NODES`. Each is **guardrailed** — slug normalized, duplicates skipped, cluster forced to a known cluster name or `None`, entities filtered to `allowed_entities`, built via `model_validate` so an invalid vocabulary raises and the node is *dropped* (keep the floor). New nodes start at `priority=0.55` ("competitors didn't anchor them").

If the model added nothing usable (`added == 0 and not merged_any`), it stays deterministic to preserve provenance. Otherwise the blueprint's `generator` becomes `llm.model` and `notes` records the augmentation.

`_synthesis_prompt(base, framework, patterns)` (`generator.py:200`) — builds the prompt. Critically, the allowed vocabularies are sourced from the **contract's own `Literal`s** via `get_args(PageType)`, `get_args(Intent)`, `get_args(JourneyStage)` (not hardcoded strings), so the prompt can never advertise a value `SitemapNode` validation would then silently drop. It lists existing pages, existing cluster names, the competitor summary, and asks for `extra_seed_questions` + `augment_nodes` (max 12) as JSON only.

**`generate_blueprint(*, topic=None, framework=None, patterns=None, llm=None, version=1) -> Blueprint`** (`generator.py:225`)
The entry point. Loads the framework if not injected; defaults topic to the framework's. Builds the deterministic blueprint; if `llm is not None and llm.enabled`, augments. Returns the blueprint stamped with the provisional `version` and `.with_hash()` applied — **the repo decides reuse-vs-bump from the content hash.** Side effects: optional network call to the LLM (Gemini's OpenAI-compatible endpoint when `provider=cloud`); logs on success/failure. The model "only *upgrades* quality; it is never a dependency."

---

### 8.8 Best-practice loader — `reference/loader.py` + `config/best_practices.yaml`

The single seam the gap analysis and recommender depend on. `DEFAULT_TARGET = 3` (`loader.py:18`) is the mid-scale fallback for a criterion missing a configured target.

- `PageArchitecture` (`loader.py:21`) — `page_type, must_have, headings, target_word_count`.
- `Reference` (`loader.py:29`) — `targets: dict[str,int]`, `architecture: dict[str,PageArchitecture]`, `intent: QueryIntentCfg`. Accessors: `target_for(criterion)` (target or `DEFAULT_TARGET`), `architecture_for(page_type)` (falls back to the `"default"` entry), `classify_intent(url, headings)` (delegates to the pure classifier).
- **`load_reference() -> Reference`** (`loader.py:57`, `@lru_cache(maxsize=1)`) — reads `best_practices.yaml`, coerces targets to ints, builds the architecture map (always ensuring a `"default"` entry), and builds the `QueryIntentCfg`. **Side effect:** one cached YAML read.

#### `config/best_practices.yaml` summary
Marked PROVISIONAL. Three blocks:
- **`targets`** — the 60% best-practice baseline (gap = `max(0, target - actual)`). All 10 rubric criteria at `4`, **except `render_accessibility: 5`** ("JS-only content is invisible to answer engines"). Matches `framework.yaml`'s `criteria_definitions` targets exactly.
- **`architecture`** — ideal structure per page-type with `must_have`, `headings`, `target_word_count`. Word targets: homepage 600, product 800, solution 900, **pillar 2000**, blog 1200, about 500, contact 200, utility 150, default 700. `default` is the fallback.
- **`query_intent`** — the heuristic config (see below): `default: informational`, plus `url_patterns` and `heading_keywords` per intent.

---

### 8.9 Query-intent classifier — `reference/query_intent.py`

A lightweight, pure URL + heading heuristic, tunable in `best_practices.yaml` with no code change. **Precedence is fixed:** `PRECEDENCE = ("commercial", "navigational", "informational")` — the most business-valuable signal wins (`query_intent.py:16`).

- `QueryIntentCfg` (`query_intent.py:19`) — `default="informational"`, `url_patterns` (intent → URL substrings), `heading_keywords` (intent → heading phrases).
- **`classify_intent(url, headings, cfg) -> str`** (`query_intent.py:28`) — checks URL substrings first (in precedence order, first match wins), then heading keywords (joined, lowercased), else returns `cfg.default`. Pure.

From `best_practices.yaml`'s `query_intent` block:

| intent | URL patterns | heading keywords |
|---|---|---|
| commercial | `/pricing, /buy, /demo, /quote, /trial, /product, /solutions, /contact` | pricing, buy now, request a demo, get started, free trial, contact sales |
| navigational | `/login, /signin, /account, /dashboard, /about, /careers, /support` | log in, sign in, contact us, careers, my account |
| informational | `/blog, /guide, /resources, /what-is, /how-to, /learn, /docs, /glossary` | what is, how to, why, overview, introduction, guide, explained |

This intent feeds `select_competitor` (gap analysis only compares against same-intent competitors) and aligns with the blueprint's `Intent` Literal.

---

### 8.10 Validated-wins feedback loop — `reference/feedback.py`

The *controlled* version of "the system evolves itself." Pages that **provably get cited** (the Independent Validator's Perplexity signal) are compared against pages that don't, per rubric criterion. Where the cited cohort consistently and materially out-tiers both the current target and the non-cited cohort, this **proposes** raising that criterion's *target*.

Two guard rails make this learning, not drift:
- **Direction.** Cited wins refine the *criteria definitions/targets* only — never the blueprint or the client's own recommendations (that would be circular validation one level up).
- **Human-gated.** Every output is a `CriteriaRefinement` with `status='proposed'`. The system never auto-applies; a human accepts/rejects, and only then does `config/best_practices.yaml` change.

Thresholds (`feedback.py:32-34`):

| Constant | Value | Meaning |
|---|---|---|
| `MIN_CITED_SAMPLE` | `3` | minimum cited pages (and minimum cited tiers per criterion) before proposing anything |
| `DEFAULT_MARGIN` | `1` | the cited cohort must beat the non-cited cohort by ≥1 tier |

- `CitationObservation` (`feedback.py:37`) — `page_id, url, cited, tiers: dict[str,int]`.
- `CriteriaRefinement` (`feedback.py:47`) — `criterion, current_target, proposed_target, rationale, evidence, status="proposed"`; `to_row()` → dict for the repo.

**`propose_criteria_refinements(observations, *, reference=None, rubric=None, min_cited=3, margin=1) -> list[CriteriaRefinement]`** (`feedback.py:64`)
Splits observations into cited/uncited. Returns `[]` immediately if fewer than `min_cited` cited pages — "proposing nothing is the correct, common outcome." Then per rubric criterion (`feedback.py:85-125`):
1. Need ≥`min_cited` cited tiers for that criterion, else skip.
2. `cited_med = median(cited tiers)`; `uncited_med = median(uncited tiers)` or `rubric.scale_min` if there are none; `current = reference.target_for(criterion)`.
3. **Signal test:** propose only if `cited_med >= current + 1` (cited cohort consistently *exceeds* the current target) **and** `(cited_med - uncited_med) >= margin` (beats the non-cited cohort).
4. `proposed = min(scale_max, max(current+1, round(cited_med)))`; skip if it doesn't exceed `current`.
5. Emit a `CriteriaRefinement` with a human-readable rationale and an `evidence` dict (`cited_n, uncited_n, cited_median, uncited_median, current_target`).

**Side effects:** none — pure and deterministic over the observations (persistence is a thin repo elsewhere, gated on human acceptance).

---

### 8.11 End-to-end summary

```
L1 competitor_patterns.extract_patterns  ─┐
                                          ├─► generator.generate_blueprint ─► Blueprint (hash-stamped)
L2 framework.load_framework  ─────────────┤        (L3 LLM optional)              │  repo pins version
                                          │                                       ▼
                                          └─────────────────────────────► processor.coverage_diff
config/best_practices.yaml ─► loader.load_reference (targets) ─┐                  │ (missing/thin pages)
config/rubric (load_rubric, weights/scale) ───────────────────┼─► gap_analysis.analyze_gap ─► GapResult
PageScore (10 criteria, 1-5) ─────────────────────────────────┘    (60% best-practice + 40% competitor)
                                                                            │
Validator citation signal ─► feedback.propose_criteria_refinements ─► CriteriaRefinement (human-gated)
                                                                  └─► (accepted) edits best_practices.yaml targets
```

The reference layer sets the *standard* (targets + ideal site); the processor *measures* against it at two altitudes (page-level gap, site-level coverage); the feedback loop *slowly raises* the standard from proven citations — but only with a human in the loop, and only the rubric targets, never the blueprint or the recommendations themselves.

## 9. Recommender & Validation — Edit Generators, Simulate/Re-score Loop, Independent Validator

This section covers the two blocks that turn a page's gap analysis into concrete, persisted, *validated* fixes:

1. **Recommender** (`src/aeo/recommender/`) — three generators that convert each deficient rubric criterion into a concrete `Recommendation` (schema JSON-LD, entity rewrites, content edits).
2. **Validation** (`src/aeo/validation/`) — the `recommend → simulate → re-score → retry (≤ max_attempts)` loop, the synthetic-page simulator, and the v4 **Independent Validator** that breaks v3's circular "grade your own homework" trap.

The whole block is **deterministic-first**: with no LLM available the system still emits grounded, criterion-specific advice and still runs the validation gate. The LLM only *upgrades* suggestion quality.

---

### 9.1 The shared data model — `recommender/models.py`

Every generator emits the same value type, `Recommendation` (`recommender/models.py:24`), a slotted dataclass:

```python
@dataclass(slots=True)
class Recommendation:
    rec_type: str               # 'schema' | 'content' | 'entity'
    criterion: str | None       # rubric criterion addressed (None = cross-cutting)
    title: str                  # short human-readable summary
    rationale: str              # WHY — grounded in the gap / Reference Layer
    payload: dict[str, Any]     # the concrete edit
    scored_by: str = "deterministic"  # 'deterministic' | model name
```

| Field | Meaning |
|---|---|
| `rec_type` | Routing tag. Module constants `SCHEMA = "schema"`, `CONTENT = "content"`, `ENTITY = "entity"` (`models.py:18-20`) match the `recommendations.rec_type` DB column vocabulary. |
| `criterion` | Which of the 10 rubric criteria this rec targets (or `None` for cross-cutting). |
| `title` / `rationale` | Human-facing summary and grounded justification. |
| `payload` | The concrete edit body. Shape varies by generator: schema recs carry `{"schema_type", "jsonld"}`; LLM recs carry `{"edits": [...]}`; advisory recs carry `{"guidance": "..."}`. |
| `scored_by` | Provenance — literal `"deterministic"`, or the model name (`llm.model`) when the LLM authored it. |

**Design intent — only two dedicated columns.** `to_payload()` (`models.py:32`) flattens `title`, `rationale`, `source` (= `scored_by`), and **everything in `payload`** into a single JSONB blob:

```python
def to_payload(self) -> dict[str, Any]:
    return {"title": self.title, "rationale": self.rationale,
            "source": self.scored_by, **self.payload}
```

Only `rec_type` and `criterion` are real DB columns; the rest lives in `recommendations.payload`. The trade-off: the DB schema never has to change when a generator adds a field, at the cost of those fields not being directly queryable.

---

### 9.2 Routing & persistence — `recommender/__init__.py`

**`recommend(bundle, gap, *, url, reference=None, llm=None, page_type=None) -> list[Recommendation]`** (`recommender/__init__.py:42`)

The fan-out entry point. It:
1. Loads the Reference Layer if not passed (`reference or load_reference()`).
2. Extracts the **deficient** criteria from the gap: `deficient = [g.criterion for g in gap.criterion_gaps]` (`__init__.py:58`). `gap.criterion_gaps` only contains criteria with `gap > 0` (per the Dual-Layer Gap Analysis), so every routed criterion is genuinely below its Reference-Layer target.
3. Routes:
   - If `"schema_markup"` is deficient → `recommend_schema(bundle, url=url)` (deterministic JSON-LD).
   - Always calls `recommend_entity(...)` and `recommend_content(...)` with the **full** `deficient` list — these two generators self-filter to the slice they own (see the `*_CRITERIA` sets), so passing the whole list is safe and keeps the router dumb.
4. Returns one flat `list[Recommendation]`.

**Inputs → outputs:** an `ExtractionBundle` + a `GapResult` → a flat list of recs. **Side effects:** none (pure aside from the `load_reference()` lru_cache read and any LLM network calls inside the generators).

**`persist(page_id, run_id, recs, *, attempt=1, score_before=None) -> list[int]`** (`recommender/__init__.py:71`)

Writes each rec to the recommendations repo via `recs_repo.create(...)`, passing `rec.to_payload()` as the JSONB body and tagging each row with `rec_type`, `criterion`, the **Validation attempt number**, and the pre-edit `score_before`. Returns the inserted row ids so the Validation loop can later update each by id.

- **Side effects:** inserts N rows into the `recommendations` table (one per rec). Lazy import of `..storage.repos.recommendations` to avoid an import cycle.

> **Routing ownership map** (the three generators partition the 10-criterion rubric with no overlap):

| Generator | Owns criteria | Module / set |
|---|---|---|
| **schema** | `schema_markup` | `schema.py` (routed by name in `recommend`) |
| **entity** | `entity_consistency` | `ENTITY_CRITERIA` (`entity.py:23`) |
| **content** | `qa_blocks`, `stats_in_html`, `content_depth`, `heading_structure`, `answer_readability`, `citation_signals`, `load_speed`, `render_accessibility` | `CONTENT_CRITERIA` (`content.py:23`) |

---

### 9.3 Schema generator (deterministic, zero-hallucination) — `recommender/schema.py`

**Why deterministic:** answer engines lean heavily on JSON-LD, and emitting *valid markup the page already supports* is the highest-confidence, zero-hallucination recommendation the system can make — so this generator uses **no LLM at all**. It builds structured data directly from already-extracted bundle content.

**`recommend_schema(bundle, *, url) -> list[Recommendation]`** (`schema.py:34`)

Reads `bundle["schema_jsonld"]`, computes `present = set(schema["types"])`, derives the URL origin, and proposes up to four block types — each only when **genuinely missing AND supported by real content**:

| Type | Builder | Proposed only when… | Concrete JSON-LD it emits |
|---|---|---|---|
| **FAQPage** | `_faq_page` (`schema.py:60`) | `"FAQPage"` not present AND `qa_blocks.qa_pairs` non-empty | `@type FAQPage` with `mainEntity` of up to `_MAX_FAQ = 10` (`schema.py:31`) `Question`/`acceptedAnswer` objects built from the detected pairs (`question` + `answer_preview`). |
| **Article** | `_article` (`schema.py:90`) | no type in `_ARTICLE_TYPES` present (`{Article, TechArticle, NewsArticle, BlogPosting}`, `schema.py:29`) AND a real **byline signal** exists (`eeat.author` or `eeat.publish_date`) | `@type Article` with `url`, optional `headline` (meta title → h1 fallback), `description`, `author` (`Person`), `datePublished`. The byline guard deliberately avoids tagging product/landing pages as articles. |
| **Organization** | `_organization` (`schema.py:125`) | `"Organization"` not present AND `entities.primary.name` exists | `@type Organization` with `name` and (if derivable) `url = origin`. |
| **BreadcrumbList** | `_breadcrumb` (`schema.py:150`) | `"BreadcrumbList"` not present AND the URL path has **≥ 2** segments AND origin known | `@type BreadcrumbList` with a `Home` item plus one `ListItem` per path segment, each `name` run through `_humanize` (`schema.py:192`: strips `.html`/`.php`, replaces `-`/`_` with spaces, Title-cases). |

`_SCHEMA_CONTEXT = "https://schema.org"` (`schema.py:30`) is the `@context` on every block. Each rec carries `payload = {"schema_type": <type>, "jsonld": <object>}` — the actual object is ready to paste into the page head. Helper `_origin(url)` (`schema.py:184`) reconstructs `scheme://netloc` (defaulting scheme to `https`).

---

### 9.4 Entity generator — `recommender/entity.py`

**Criterion owned:** `entity_consistency` only (`ENTITY_CRITERIA = {"entity_consistency"}`, `entity.py:23`).

**The problem it fixes:** Entity Consistency scores the ratio of canonical brand mentions to first-person language ("we/our/us"). Cybersecurity marketing pages routinely run first-person ~2:1 over the brand name, which reads as *anonymous* to an answer engine trying to attribute a claim to a named organization.

**`recommend_entity(bundle, targets, *, reference=None, llm=None) -> list[Recommendation]`** (`entity.py:26`)

Self-filters `targets` to its one owned criterion. For that criterion: if `llm` is enabled, try `_llm_edit`; otherwise (or on LLM failure) fall back to `_advisory`.

- **`_state(bundle)`** (`entity.py:45`) extracts `(primary entity name, entity_count, first_person_count, ratio)` from `bundle["entities"]`.
- **`_grounding(...)`** (`entity.py:56`) builds the rationale string, citing the target (`reference.target_for("entity_consistency")` → **4** per `best_practices.yaml:17`) and the actual mention counts.
- **`_llm_edit`** (`entity.py:80`) prompts an AEO editor system role to return JSON `{"summary", "edits": [before/after rewrite strings]}`, foregrounding the resolved brand name. Result payload: `{"edits": [...], "primary_entity": name}`, `scored_by = llm.model`.
- **`_advisory`** (`entity.py:115`) is the deterministic fallback and **names the exact brand** so the advice is grounded, not generic. Two branches:
  - `entity_count == 0` → "The page never names {brand}. State the organization's name explicitly…"
  - else → "Replace first-person phrasing (we/our/us) with '{brand}'… aim for at least parity so the brand — not an anonymous 'we' — owns each claim."

---

### 9.5 Content generator (LLM-authored, deterministic-first) — `recommender/content.py`

**Criteria owned** (`CONTENT_CRITERIA`, `content.py:23`): `qa_blocks`, `stats_in_html`, `content_depth`, `heading_structure`, `answer_readability`, `citation_signals`, `load_speed`, `render_accessibility`.

**LLM-eligible subset** (`_LLM_CRITERIA`, `content.py:35`): only `qa_blocks`, `stats_in_html`, `content_depth`, `heading_structure`, `answer_readability` — the content-shaped ones worth a rewrite. The remaining three (`citation_signals`, `load_speed`, `render_accessibility`) are **technical/advisory only** — they get a static advisory even when the LLM is on, because they're fixes to engineering/page plumbing rather than prose the model should author.

**`recommend_content(bundle, targets, *, reference=None, llm=None, page_type=None) -> list[Recommendation]`** (`content.py:79`)

For each target in `CONTENT_CRITERIA`: if it's in `_LLM_CRITERIA` and `llm` is enabled, attempt `_llm_edit`; otherwise/on failure, emit `_advisory`. Every targeted criterion always yields exactly one rec — `recs.append(rec or _advisory(...))` guarantees a fallback (`content.py:95`).

- **`_grounding(reference, criterion, page_type)`** (`content.py:99`) builds the best-practice context: the per-criterion target (e.g. `qa_blocks` → 4), and when `page_type` is known, the page-type's `must_have` list and `target_word_count` from the Reference Layer's architecture entry.
- **`_excerpt(bundle)`** (`content.py:111`) packs a compact page snapshot into the prompt: title, H1, up to 8 H2 headings, word count.
- **`_llm_edit`** (`content.py:126`) uses an AEO-editor system prompt demanding **specific, concrete** edits as JSON `{"summary", "edits": [...]}`; payload `{"edits": [...]}`, `scored_by = llm.model`.
- **`_advisory`** (`content.py:163`) emits a static, criterion-specific string from the `_ADVISORY` table (`content.py:43`). The `content_depth` advisory is further enriched with the page-type word-count goal when known.

The eight static advisories (`content.py:43-76`) encode the actual AEO guidance, e.g.:

| Criterion | Advisory gist (verbatim intent) |
|---|---|
| `qa_blocks` | FAQ section: 3-6 audience-phrased questions, each answered in 2-4 concise sentences under the question heading. |
| `stats_in_html` | Concrete, sourced stats as plain HTML text (percentages, counts, CVE/CVSS, dates) — engines preferentially quote specific numbers. |
| `content_depth` | Add methodology, data, worked examples; thin content rarely earns citations. |
| `heading_structure` | Rewrite H2/H3 as user questions or named concepts under a single descriptive H1. |
| `answer_readability` | Shorter sentences, one idea per paragraph, clear sub-sections so an engine can lift a clean passage. |
| `citation_signals` | Author byline + credentials, visible publish/updated date, links to NIST/CISA/MITRE/OWASP. |
| `load_speed` | Mobile perf: compress images, defer non-critical JS, cache static assets. |
| `render_accessibility` | Serve key content in server-rendered HTML, not client-side JS engines may never execute. |

---

### 9.6 Validation public surface — `validation/__init__.py`

Re-exports the loop, its result type, the simulator, and the independent validator (`validation/__init__.py:13-29`):

- Statuses: `STATUS_IMPROVED`, `STATUS_COULD_NOT_IMPROVE`, `STATUS_NO_ACTION`; review routing `REVIEW_NEEDED`, `REVIEW_NONE`.
- `ValidationOutcome`, `validate_page` (the loop).
- `apply_recommendation` (the simulator).
- `IndependentVerdict`, `IndependentCheck`, `CitationVerdict`, `validate_independent`, `derive_question`.

---

### 9.7 The validation loop — `validation/validator.py`

**Core question it answers:** *does the proposed fix actually move the score?*

**`validate_page(bundle, gap, *, url, reference=None, rubric=None, llm=None, page_type=None, max_attempts=None, persist=True, recommend_fn=recommend, score_fn=score_page) -> ValidationOutcome`** (`validator.py:81`)

Algorithm:

1. **Baseline.** Score the *original* bundle through the same 10-criterion rubric: `det_before = score_fn(bundle, run_id, llm=_DETERMINISTIC_LLM, rubric=rubric).total` (`validator.py:103`).
2. **No-action short-circuit.** If `gap.criterion_gaps` is empty → return `STATUS_NO_ACTION`, `improved=False`, `attempts=0`, `review_status=REVIEW_NONE` (`validator.py:106-116`). Nothing to fix, nothing to review.
3. **The retry loop** (`validator.py:123-138`), up to `max_attempts`:
   - `best_recs = recommend_fn(bundle, gap, ...)` — re-run the recommender.
   - `synthetic = copy.deepcopy(bundle)` — never mutate the real bundle.
   - For each rec: `apply_recommendation(synthetic, rec, rubric=rubric, reference=reference)` — mutate the synthetic copy's signals.
   - `det_after = score_fn(synthetic, ...).total` — re-score the synthetic page.
   - **If `det_after > det_before` → `improved = True`, break.**
   - **If the LLM is off (`llm is None or not llm.enabled`) → break after the first attempt.** (See "Why retry only helps with an LLM" below.)
4. **Classify & route:** `status = STATUS_IMPROVED if improved else STATUS_COULD_NOT_IMPROVE`; per-rec DB status `_REC_VALIDATED` ("validated") if improved else `_REC_NEEDS_REVIEW` ("needs_review") (`validator.py:140-141`).
5. **Log** a structured `validation_complete` event (page/run ids, status, attempts, before/after, rec count).
6. **Persist** (if `persist=True` and there are recs) via `_persist`.
7. Return the `ValidationOutcome`.

> **Important branch nuance:** the page-level `review_status` in the returned outcome is **always `REVIEW_NEEDED`** when there *are* gaps (`validator.py:166`) — both the "improved → apply these" and "could-not-improve" paths route to Human Review. `REVIEW_NONE` only happens on the no-action short-circuit. "Human Review" here is a *status, not a UI*: improved recs are `status='validated'` (ready to apply), could-not-improve recs are `status='needs_review'`.

**`_DETERMINISTIC_LLM = LLMClient(LLMCfg(enabled=False))`** (`validator.py:51`) — both sides of the before/after comparison are scored with a deliberately **disabled** client. **Why:** the gate must measure the *edits'* effect, never run-to-run LLM scoring noise. The scoring aggregator substitutes the real client when handed `llm=None`, so the loop passes this disabled client explicitly to force determinism. (Note: the *recommender* still gets the real `llm`; only the *scorer* is forced deterministic.)

**Why retry only helps with an LLM** (the design comment at `validator.py:14-22`): a deterministic recommender produces identical output every attempt, so retrying it re-applies the same edit and re-scores to the same number — futile. Hence the early `break` when the LLM is off. With an LLM enabled, a transient model failure on one attempt may succeed on the next, or yield a more concrete edit, so retries can genuinely vary the outcome. The Recommender is kept **stateless** — "feeding back the failed attempt" is realized purely as re-invocation; the deterministic signal-based gate gains nothing from injecting failure text into the prompt.

**`max_attempts`** defaults to `get_settings().validation.max_attempts` = **3** (`settings.py:110`), floored to `max(1, ...)`. The cap guarantees a stubborn page can never spin forever.

**`ValidationOutcome`** (`validator.py:67`) — slotted dataclass:

| Field | Meaning |
|---|---|
| `page_id`, `run_id` | identity |
| `status` | `improved` / `could_not_improve` / `no_action` |
| `improved` | bool gate result |
| `attempts` | how many recommend→simulate→score cycles ran |
| `score_before`, `score_after` | deterministic rubric totals (int) before / on the synthetic page |
| `review_status` | `needs_review` / `none` (feeds `page_reports.review_status`) |
| `recommendations` | the `best_recs` list |
| `rec_ids` | persisted row ids (`[]` if not persisted) |

**`_persist(...)`** (`validator.py:172`) — writes the recs via the recommender's `persist`, then stamps **each** row with its validation outcome through `recs_repo.set_validation(rid, status=rec_status, validated=improved, score_after=score_after)`.

- **Side effects (loop):** structured log line; N row inserts + N `set_validation` updates on `recommendations`; **no** mutation of the real `bundle` (deep-copied), **no** network (scorer uses the disabled LLM). The recommender it invokes *may* make LLM network calls.

---

### 9.8 The synthetic-page simulator — `validation/simulate.py`

**Key insight (`simulate.py:1-9`):** scorers never re-parse HTML — they read the **extraction bundle**. So "apply the edit and re-score" means: mutate a *copy* of the bundle so its extracted signals reflect what the rec would achieve, then run the same rubric over it.

**`apply_recommendation(bundle, rec, *, rubric, reference) -> bool`** (`simulate.py:46`)

Dispatches one rec to its applier; returns `True` if a signal changed. Logic:
- `rec_type == SCHEMA` → `_apply_schema`.
- Otherwise, **concrete-edits-only gate**: if `"edits"` not in `rec.payload` → return `False` (no-op). A bare advisory has nothing to apply.
- Look up `_APPLIERS[criterion]`; if none → no-op.
- Compute `target = int(reference.target_for(criterion))` and call the applier.

**Two faithfulness rules** that keep this honest rather than wishful (`simulate.py:9-27`):

1. **Bounded to target.** Each applier raises a criterion's binding signal only as far as the Reference-Layer *target* tier — **never to a perfect 5**. Consequence: a page whose only deficiency is competitor pressure (already at/above target) will **not** "improve" in simulation — which is correct, and is exactly what drives the retry/flag path.
2. **Concrete edits only.** Only recs carrying a concrete artifact (schema JSON-LD, or an LLM `edits` list) are simulated. Advisory-only recs are no-ops → the loop routes the page to Human Review instead of claiming an unsubstantiated improvement.

**Purity:** no DB, no network. Mutates the bundle **in place** (callers pass a deep copy).

**The appliers** (each brings its binding signal up to `target`):

| Criterion | Applier | What it raises, and to what | Source |
|---|---|---|---|
| `schema_markup` | `_apply_schema` | Appends `schema_type` to `schema_jsonld.types` if absent; bumps `block_count` to ≥ len(types). Deliberately does **not** touch `invalid_blocks` (the added block is well-formed; no malformed penalty incurred). | `simulate.py:105` |
| `qa_blocks` | `_apply_qa` | Sets `qa_blocks.pair_count` to `_QA_PAIRS_FOR_TIER[target]`. | `simulate.py:122` |
| `stats_in_html` | `_apply_stats` | Sets `stats.count` to the rubric's `stats_in_html` tier value for `target`. | `simulate.py:132` |
| `entity_consistency` | `_apply_entity` | Raises `entities.ratio` to the tier value, and bumps `entity_count = max(cur, 1, ceil(new_ratio * first_person))` so the evidence stays coherent (replacing "we" with the brand raises the brand count). | `simulate.py:142` |
| `heading_structure` | `_apply_heading` | Raises `headings.h23_question_ratio` to the tier value AND clears `missing_h1` / `template_h1` defects (the rewrite fixes those too). | `simulate.py:160` |
| `content_depth` | `_apply_depth` | Raises `readability.word_count` to `_DEPTH_WORDS_FOR_TIER[target]`. | `simulate.py:178` |
| `answer_readability` | `_apply_readability` | Raises `readability.flesch_reading_ease` to the flesch tier; ensures `word_count ≥ min_word_count`; caps `avg_sentence_length` at `max_avg_sentence_len`; and bumps `chunker.chunk_count` to 2 if ≤ 1 (removes the monolithic-content penalty). | `simulate.py:189` |

**The two hard-coded band tables** (`simulate.py:42-43`) mirror bands that live *inside the scorers* (not config), with the comment "Keep in sync if those bands ever change":

| Tier (target) | `_QA_PAIRS_FOR_TIER` (min Q&A pairs) | `_DEPTH_WORDS_FOR_TIER` (min words) |
|---|---|---|
| 2 | 1 | 200 |
| 3 | 2 | 400 |
| 4 | 3 | 800 |
| 5 | 5 | 1500 |

The other appliers read tiers from the **rubric config** via `_tiers(rubric, name, key="tiers")` (`simulate.py:85`), with hard-coded fallbacks if the rubric lacks them, e.g. `stats_in_html` → `{1:0, 2:1, 3:3, 4:6, 5:10}`, `entity_consistency` → `{1:0.0 … 5:2.5}`, `heading_structure` → `{1:0.0 … 5:0.65}`, `answer_readability` flesch → `{1:0 … 5:55}`. Helpers `_section` (creates the sub-dict if absent), `_as_float`, `_as_int` keep the mutations type-safe.

Since the Reference Layer targets default to **4** for these criteria (`best_practices.yaml:14-23`; `render_accessibility = 5`), in practice a single deficient criterion will be simulated up to its tier-4 signal level.

---

### 9.9 The Independent Validator — `validation/independent.py`

**The v3 problem this fixes (`independent.py:1-24`):** v3's validator re-scored the recommendation against the **same** rubric the recommender optimized — same signals, same blind spots. That's *circular*: the recommender effectively grades its own homework, so a "pass" proves nothing except internal self-consistency. The v4 Independent Validator checks signals the recommender does **not** directly optimize, so passing it is *real* evidence.

**Three deterministic, non-circular checks** (always run, no network):

| Check | Function | Passes when | Why it's independent of the rubric |
|---|---|---|---|
| `tldr_under_50_words` | `_check_tldr` (`independent.py:102`) | `0 < lead ≤ max_words` where `lead` = word count of the **first chunk** (`_lead_answer_words`, `independent.py:76`) and `max_words = DEFAULT_TLDR_MAX_WORDS = 50` (`independent.py:33`) | There must be a *liftable lead answer*. The rubric never scores "is there a ≤50-word TL;DR". |
| `h1_is_question` | `_check_h1_question` (`independent.py:112`) | the H1 exists and `h1.rstrip().endswith("?")` | The rubric scores H2/H3 question *ratio* and H1 *defects* — never "the H1 itself is the user's question". |
| `valid_jsonld_present` | `_check_valid_jsonld` (`independent.py:122`) | `block_count ≥ 1` AND `invalid_blocks == 0` | Stricter than the `schema_markup` tier, which rewards type *coverage*; this demands at least one block and **zero** malformed ones. |

**Real-world signal (optional, never a hard gate):** a **Perplexity citation test** — does the page's domain actually get cited for its target question? Run only when a Perplexity client is supplied and `enabled` (`independent.py:153`). It calls `perplexity.cited(target_question, target_url=url)` and records a `CitationVerdict` (`independent.py:43`) with `available`, `cited`, `question`, `citations`, `matched`. **Why it's never a gate:** a brand-new page won't be cited yet and shouldn't be failed for that — so it's a signal feeding the "validated-wins" loop, not a pass/fail requirement.

**`derive_question(bundle) -> str | None`** (`independent.py:91`) picks the target question for the citation test: a question-shaped H1 (ends with `?`), else the meta title, else the H1, else `None`.

**`validate_independent(bundle, *, url, question=None, perplexity=None, tldr_max_words=50) -> IndependentVerdict`** (`independent.py:134`):
1. Run the three deterministic checks.
2. `deterministic_passed = all(c.passed for c in checks)`.
3. If a Perplexity client is enabled and a target question exists, run the probe and build a `CitationVerdict`; on a `None` probe, record `available=False`.
4. Return an `IndependentVerdict`.

**`IndependentVerdict`** (`independent.py:52`):
- `.passed` property = `deterministic_passed` only — **the authoritative gate is the conjunction of the three deterministic checks**; the citation result is a signal, not a hard requirement (`independent.py:59-64`).
- `.to_detail()` serializes everything (each `IndependentCheck` via `asdict`, plus the citation) for the report/DB.

**Config knobs** (`PerplexityCfg`, `settings.py:121-130`): `enabled` (default `False` — with no key the validator falls back to deterministic checks and **never fails a page for a missing key**), `api_key`, `base_url = "https://api.perplexity.ai"`, `model = "sonar"`, `timeout_sec = 60`. The loop-level switch `ValidationCfg.independent_enabled` (default `True`, `settings.py:114`) decides whether the independent gate runs at all; off → the v3 circular re-score gate alone decides review routing.

**Purity / side effects:** pure over an extraction bundle plus an injected Perplexity client. The deterministic checks make no network calls; the optional citation test is the only network I/O, and only when explicitly enabled.

---

### 9.10 How the pieces compose (the WHY in one paragraph)

For each deficient page: `recommend` fans the page's `criterion_gaps` out to the three generators (schema = deterministic JSON-LD, entity = brand-vs-"we" rewrites, content = LLM edits + advisories), all deterministic-first. `validate_page` then proves the fix *before* trusting it: it deep-copies the bundle, applies each rec to the copy's *signals* (bounded to the Reference target, concrete edits only), re-scores through the identical rubric with a **forced-deterministic** LLM, and only marks recs `validated` if the synthetic total beats the baseline — retrying only when an LLM can actually vary the output, capped at 3. Finally, the **Independent Validator** sidesteps v3's circularity by judging signals the recommender never optimized (a liftable TL;DR, a question-shaped H1, strictly-valid JSON-LD) plus an optional real-world Perplexity citation probe — so a "pass" is external evidence, not the recommender grading itself.

## 10. Reporting, Observability & Utilities

This section covers the **deliverable layer** (per-page and site-level reports), the **observability layer** (`agent_traces` tracing and the per-page Error Sink that isolates failures so one bad page never kills a run), and the small **utility kit** (URL, text, HTML, hashing, timing) that every extractor and scorer leans on.

Everything in the report and utility modules is **pure and deterministic** — it transforms already-computed objects into structured records or strings. The only side-effecting helpers are the thin `persist_*` functions (which delegate to repos) and the tracing/error-sink writers (which write `agent_traces` rows). All persistence goes through the storage repos (Section on storage), so the modules here never touch SQL directly.

---

### 10.1 Per-page report — `src/aeo/report/builder.py`

This is the system's **final per-page deliverable**: it folds everything the pipeline learned about one page (`score`, `gap`, `recommendations`, `validation`, and the new independent verdict) into a single `PageReport` — a summary line + a `sections` JSONB blob + a `review_status`.

#### Review-status domain and the validation→review routing table

The module declares the four legal values of `page_reports.review_status` (matching the DB `CHECK` constraint) at `report/builder.py:38-41`:

| Constant | Value | Meaning |
|---|---|---|
| `REVIEW_PENDING` | `"pending"` | Recommendations exist and were (or weren't) validated; a human must approve applying them. |
| `REVIEW_APPROVED` | `"approved"` | Nothing to fix — no action required. |
| `REVIEW_REJECTED` | `"rejected"` | (Defined for the domain; set by humans downstream, not by `build_report`.) |
| `REVIEW_COULD_NOT_IMPROVE` | `"could_not_improve"` | Automatic fixes could not raise the score; needs manual attention. |

The mapping from a `ValidationOutcome.status` to a review status lives in `_REVIEW_FOR_STATUS` (`report/builder.py:44-48`):

```python
_REVIEW_FOR_STATUS = {
    STATUS_IMPROVED:           REVIEW_PENDING,            # validated; a human approves applying it
    STATUS_COULD_NOT_IMPROVE:  REVIEW_COULD_NOT_IMPROVE,
    STATUS_NO_ACTION:          REVIEW_APPROVED,           # nothing to fix
}
```

**Design intent:** "Human Review" is not a UI — it is a DB flag plus a report section. A page that the validator *improved* is not auto-applied; it routes to `pending` so a human approves the edit. A page with nothing to fix is `approved` (no human time spent). A page the validator could not improve is flagged `could_not_improve` so a human looks at it. Note `REVIEW_REJECTED` is never produced here — it is a human-set terminal state.

#### `PageReport` (dataclass, `report/builder.py:51-58`)

```python
@dataclass(slots=True)
class PageReport:
    page_id: int
    run_id: int
    url: str
    summary: str
    sections: dict[str, Any]
    review_status: str = REVIEW_PENDING
```

A plain value object. `sections` is the JSONB payload written to `page_reports.sections`; `summary` is the human-readable one-liner; `review_status` defaults to `pending`.

#### `build_report(...) -> PageReport` (`report/builder.py:61-105`)

```python
def build_report(*, url, score: PageScore, gap: GapResult | None = None,
                 validation: ValidationOutcome | None = None,
                 recommendations: list[Recommendation] | None = None,
                 page_type: str | None = None, intent: str | None = None,
                 independent: IndependentVerdict | None = None) -> PageReport
```

**What it does, step by step:**

1. **Resolve the recommendation list.** If `recommendations` is passed explicitly it wins; otherwise it falls back to `validation.recommendations` (the validator carries the recs it tested); otherwise `[]` (`builder.py:73-75`). This lets the caller hand recs in directly or let the validator's tested set flow through.
2. **Order recs by gap priority** via `_order_by_gap_priority(recs, gap)` (see below). The report is a *remediation document*, so most-impactful-first ordering is load-bearing.
3. **Derive review status** from the validation outcome through `_REVIEW_FOR_STATUS`; if there's no validation, default to `REVIEW_PENDING` (`builder.py:78-82`).
4. **Build the six core sections** into the `sections` dict (`builder.py:84-91`): `overview`, `scores`, `gap`, `recommendations`, `validation`, `human_review`.
5. **Conditionally add `independent_validation`** (the v4 addition) only when an `IndependentVerdict` was supplied: `sections["independent_validation"] = independent.to_detail()` (`builder.py:92-95`). This is the **non-circular** verdict — see 10.1.1.
6. **Build the summary string** via `_summary(...)` and return the `PageReport` keyed on `score.page_id` / `score.run_id` (`builder.py:96-105`).

**Inputs → outputs:** already-computed objects (`PageScore`, `GapResult`, `ValidationOutcome`, `list[Recommendation]`, `IndependentVerdict`) → one `PageReport`. **No side effects** (pure transform; no DB, no network, no files).

#### The six (or seven) sections

**`_overview` (`builder.py:130-140`)** — identity + headline score:

```json
{"url", "page_type", "intent", "total", "max_possible",
 "score_pct", "priority_tier", "rubric_version"}
```

`score_pct` comes from `_pct(total, max_possible)` = `round(total/max*100, 1)`, guarded to `0.0` when `max_possible` is falsy (`builder.py:126-127`).

**`_scores` (`builder.py:143-155`)** — the 10 criterion tiers rendered **weakest first**. Each row is `{criterion, value, scored_by, notes}`, and rows are sorted by `(value, criterion)` — lowest score first, ties broken alphabetically. This sort is the remediation order: the reader sees the worst criteria at the top.

**`_gap` (`builder.py:158-179`)** — the Dual-Layer Gap Analysis. Returns `None` if no gap was computed. Otherwise:

```json
{"bestpractice_gap", "competitor_gap", "overall_gap",
 "competitor_available": <competitor_gap is not None>,
 "intent",
 "deficiencies": [ {criterion, actual, target, bestpractice_gap,
                    competitor, competitor_gap, priority}, ... ]}
```

`competitor_available` is the boolean truth of "was a competitor benchmark present?" — derived from whether `competitor_gap` is `None`. The deficiency rows are passed through verbatim from `gap.criterion_gaps` (already priority-ordered upstream).

**`recommendations` (built inline at `builder.py:88`, item shape from `_rec`, `builder.py:182-191`)** — a list of `{criterion, type, title, rationale, source, detail}`. `type` is `rec.rec_type`, `source` is `rec.scored_by`, and `detail` is a shallow copy of `rec.payload` (the concrete, ready-to-paste artifact — edits, guidance, or schema markup).

**`_validation` (`builder.py:194-205`)** — before→after evidence, `None` if no validation:

```json
{"status", "improved", "attempts", "score_before", "score_after",
 "delta": score_after - score_before, "max_possible"}
```

`delta` is computed here so the renderer doesn't have to. `max_possible` is threaded in from the score so the validation block is self-describing.

**`_human_review` (`builder.py:208-227`)** — turns the routed review status into a reason + an `action_required` flag. Branches:

| `review_status` | `reason` | `action_required` |
|---|---|---|
| `could_not_improve` | "Automatic fixes could not raise the score after N attempt(s); needs manual review." | `True` |
| `approved` | "No deficiencies found; no action required." | `False` |
| else (`pending`) | If validation improved: "Recommendations validated (simulated +Δ); awaiting human approval to apply." Otherwise: "Recommendations proposed; awaiting human review." | `True` |

`N` is `validation.attempts` (0 if no validation) and `Δ` is `score_after - score_before`.

#### 10.1.1 The `independent_validation` section (v4, non-circular)

When `build_report` is given an `IndependentVerdict`, it serializes it with `independent.to_detail()` (`validation/independent.py:66-73`):

```json
{"passed", "deterministic_passed", "question",
 "checks": [{"name", "passed", "detail"}, ...],
 "citation": {"available", "cited", "question", "citations", "matched"} | null}
```

**Why this section exists (design intent):** the recommender and the rubric scorer share the same signals, so re-scoring a fixed page with the same rubric is *circular* — it can only confirm what the recommender already optimized for. The independent validator checks **concrete, controllable properties that don't depend on the rubric** (`validation/independent.py:5-22`). `passed` is the authoritative gate and equals `deterministic_passed` — the conjunction of the deterministic checks (`independent.py:60-64`). The **Perplexity citation test is a signal, not a hard requirement**, because a freshly-fixed page won't be cited yet. Example deterministic check (`independent.py:102-109`): `tldr_under_50_words` — the page's lead answer must be `0 < lead <= 50` words (`DEFAULT_TLDR_MAX_WORDS = 50`, `independent.py:33`).

#### `_summary(...)` (`builder.py:230-253`)

Builds the one-line headline, e.g.:

> `https://x scored 34/50 (68.0%, high priority). 3 deficiencies, 5 recommendation(s). Validated: simulated score rises to 41/50 (+7). Review: pending.`

It pluralizes "deficiency/ies", appends a "Validated: …" clause only when `validation.improved`, appends a "could not raise the score" clause when the status is `STATUS_COULD_NOT_IMPROVE`, and always ends with `Review: <status>.`

#### `_order_by_gap_priority(recs, gap)` (`builder.py:256-265`)

The ordering rule for the recommendations list. If there is no gap, the original order is preserved. Otherwise it builds `rank = {criterion: index}` from the gap's already-priority-sorted `criterion_gaps`, and sorts recs by that rank. Recs whose criterion isn't on the deficiency list (or whose `criterion` is `None`) get `fallback = len(rank)` — i.e., they sink to the bottom in their original relative order (stable sort). **Trade-off:** rec impact is inferred from the gap's priority, not recomputed — a single source of truth for "what matters most on this page."

#### `persist_report(report) -> int` (`builder.py:108-118`)

The only side-effecting function in the module. It lazy-imports `storage.repos.reports` (to keep the builder import-light and avoid a DB dependency at module load) and calls `reports_repo.put(page_id, run_id, summary, sections, review_status=...)`, which **upserts into `page_reports` keyed on `(page_id, run_id)`** and returns the row id (`storage/repos/reports.py:11-19`). **DB table touched:** `page_reports`.

---

### 10.2 Per-page renderer — `src/aeo/report/render.py`

Turns a `PageReport` (or an equivalent `page_reports` DB-row dict) into the plain-text block that `aeo report` prints. **Deliberately colour-free** so the output pipes/redirects cleanly; the CLI adds emphasis around it (`render.py:1-7`). `_RULE` is a 72-character `=` divider (`render.py:15`).

#### `render_report(report) -> str` (`render.py:18-53`)

Accepts either the dataclass or a dict. It unpacks `(summary, sections, review)` via `_unpack` (`render.py:61-67`) — for a dict, `review_status` falls back to `sections["human_review"]["status"]`, then `"pending"`. It then assembles the block in fixed order, **omitting optional sections when empty**:

1. Title rule + `AEO/SEO REPORT - <url>` (url from `overview`, default `(unknown url)`).
2. Wrapped summary (`textwrap.wrap` at width 72, `render.py:70-73`).
3. `_overview_block` (always).
4. `_scores_block` (always).
5. `_gap_block` (only if `gap` present).
6. `_recs_block` (only if recs present).
7. `_validation_block` (only if validation present).
8. `_independent_block` (only if `independent_validation` present).
9. `_review_block` (always).

**Notable rendering details:**

- **Score bar (`_scores_block`, `render.py:90-96`):** each criterion renders an ASCII bar `"*" * value + "." * (5 - value)` — a 0–5 scale visualized in 5 cells, e.g. `[***..] 3/5  content_depth`.
- **Gap block (`render.py:99-117`):** prints best-practice / competitor / overall gaps; competitor shows `n/a (no competitor)` when `None`; deficiencies print `criterion actual -> target target` with an optional `, competitor X` and `(priority N)`.
- **`_edit_lines` (`render.py:131-139`)** surfaces the concrete artifact of a recommendation, in priority order: if `detail["edits"]` is a list, print each; else if `detail["guidance"]` exists, print it; else if `detail["schema_type"]` exists, print `Add <schema_type> JSON-LD (ready-to-paste markup in payload)`; else nothing. This is how the reader sees *what* to change, not just *that* something should change.
- **Validation block (`render.py:142-152`):** shows `score_before -> score_after (±delta)` with the sign forced (`+7`, `-2`).
- **Independent block (`render.py:155-165`):** `Verdict : PASS/FAIL`, then each check as `[ok]`/`[X ]` `name detail`. If a citation test ran (`citation.available`), it prints `Perplexity citation: CITED|not cited for '<question>'`.
- **Review block (`render.py:168-172`):** `Status : <status> [ACTION REQUIRED|no action]` plus the reason line.

**Inputs → outputs:** `PageReport | dict` → `str`. **No side effects.**

---

### 10.3 Site-level report — `src/aeo/report/site_builder.py`

The **v4 second deliverable**. Where the per-page report answers "is this room up to code?", the site report answers "which rooms are missing, and how does the whole house score?" (`site_builder.py:1-13`). It folds the **Coverage Diff** (missing pages + thin clusters → net-new content briefs) and a **per-page rollup** (score distribution, worst pages, review tally) into one record, pinned to the blueprint version it was measured against. Pure transform; persistence is a thin repo helper.

#### `SiteReport` (dataclass, `site_builder.py:24-31`)

```python
@dataclass(slots=True)
class SiteReport:
    run_id: int
    target_id: int | None
    blueprint_id: int | None
    summary: str
    sections: dict[str, Any]
```

#### `build_site_report(...) -> SiteReport` (`site_builder.py:84-134`)

```python
def build_site_report(*, blueprint: Blueprint, coverage: CoverageDiffResult,
                       pages: list[dict[str, Any]], run_id: int,
                       target_id: int | None = None,
                       blueprint_id: int | None = None) -> SiteReport
```

Assembles three sections:

- **`overview`** (`site_builder.py:103-112`): topic + `blueprint_version` + `blueprint_generator`, `coverage_pct`, `ideal_pages` (= `coverage.total_nodes`), `covered_pages` (= `coverage.matched_count`), `missing_pages` (= `len(coverage.missing)`), and `pages_analyzed` (from the rollup).
- **`coverage_gaps`** (`site_builder.py:113-117`): `missing_count`, `thin_clusters`, and `new_page_recommendations` (the briefs). Each thin-cluster row is `{cluster, present, target, shortfall}` mapped from `coverage.thin_clusters` (`site_builder.py:97-100`); `target` is the cluster's `min_pages` and `shortfall` is how many more pages are needed.
- **`page_rollup`** (from `_page_rollup`, below).

The **summary** (`site_builder.py:121-126`) reads, e.g.:

> `Cloud Security: blueprint v3 — site covers 18/40 ideal pages (45.0%). 22 missing page(s), 3 thin cluster(s). 18 page(s) analyzed, avg 61.2%.`

**Inputs → outputs:** a `Blueprint`, a `CoverageDiffResult`, and per-page summary rows → one `SiteReport`. **No side effects.**

#### `_page_rollup(pages) -> dict` (`site_builder.py:33-61`)

Computes the per-page distribution from summary rows, where each row is `{url, total, max_possible, priority_tier, review_status}`. Returns:

```json
{"pages", "avg_score_pct", "by_priority": {tier: count},
 "by_review": {status: count}, "worst_pages": [{url, total, priority_tier}, ...]}
```

- **Empty input** returns a zeroed shell (`pages: 0`, empty maps, empty list) — `site_builder.py:36-37`.
- **`avg_score_pct`** is the mean of each page's `round(total/max*100, 1)` (0.0 when a page's `max_possible` is falsy), then rounded to 1 decimal (`site_builder.py:44-45,57`).
- **`worst_pages`** is the 10 lowest-`total` pages (`sorted(...)[:10]`, `site_builder.py:50`). The magic number here is **10** — the worst-10 are kept in the record (the renderer later shows only the top 5).

#### `new_page_briefs(coverage) -> list[dict]` (`site_builder.py:64-81`)

Turns each missing blueprint node into a **net-new content brief** — the site-level "missing-page recommendation" — iterating `coverage.missing_by_priority()` so **highest blueprint-priority comes first**. Each brief surfaces the blueprint node's authoring spec:

```json
{"slug", "title", "page_type", "intent", "journey_stage", "cluster",
 "priority", "required_entities", "seed_questions", "why": <rationale>}
```

This is what tells a content team exactly what page to write, for what intent/journey stage, with which entities and seed questions.

#### `render_site_report(report) -> str` (`site_builder.py:137-188`)

Plain-text rendering for `aeo site-report`. Accepts the dataclass or a DB-row dict. Layout: a 72-`=` rule + `SITE AEO REPORT - <topic> (blueprint v<version>)`, the summary, then a **COVERAGE** block (ideal / covered+pct / missing), an optional **thin-clusters** list (`cluster present/target (need shortfall more)`), an optional **NET-NEW CONTENT** list (first **20** briefs, each `[page_type] slug (priority)` plus the first seed question), and a **PAGE ROLLUP** block (count + avg %, `by_priority`, `by_review`, and the worst **5** pages). The two caps — `briefs[:20]` (`site_builder.py:171`) and `worst[:5]` (`site_builder.py:185`) — keep the printed report scannable while the JSONB record retains the fuller lists. **No side effects.**

> Site-report persistence lives in `storage/repos/site_reports.py:11` (`put(report)`), which **upserts into `site_reports` keyed on `run_id`** and preserves `created_at` on refresh (first-insert time). `site_builder.py` itself does not call it.

---

### 10.4 Observability — `src/aeo/obs/`

The `obs` package is the cross-cutting observability + failure-isolation layer. Its `__init__` (`obs/__init__.py`) re-exports `trace_step` / `StepHandle` (tracing) and `page_guard` / `record_failure` / `PageOutcome` (the Error Sink). The principle: **tracing is the single writer of `agent_traces`**, and **one bad page never kills a run**.

#### 10.4.1 Tracing — `src/aeo/obs/tracing.py`

`trace_step` is the **single writer of `agent_traces` rows**. It wraps one unit of the per-page pipeline (Analyze → Gap → Recommend → Validate → Report) and does four things (`tracing.py:1-19`): binds context for structured logging, times the step, writes a success or failed trace row, and re-raises on error so the Error Sink can decide whether to skip the page.

**`StepHandle` (dataclass, `tracing.py:38-45`):** a mutable handle yielded into the `with` body so the body can record runtime facts discovered mid-step:

```python
@dataclass
class StepHandle:
    model: str | None = None   # the LLM model actually used
    tokens: int | None = None  # token count
```

**`trace_step(agent, *, run_id=None, page_id=None, step=None, model=None)` (context manager, `tracing.py:55-91`):**

```python
with trace_step("recommender", run_id=r, page_id=p, step="generate") as h:
    h.model = "claude-..."   # filled in mid-step
    h.tokens = 1234
    ...                      # do the work
```

Flow:
1. Construct a `StepHandle(model=model)`.
2. `structlog.contextvars.bind_contextvars(agent, step, run_id, page_id)` — so **every log line emitted inside the step automatically carries those four keys** (`_CTX_KEYS = ("agent", "step", "run_id", "page_id")`, `tracing.py:35`). Logs `step_started`.
3. `yield handle`; the body runs.
4. **On success:** compute `duration_ms = int((perf_counter()-start)*1000)`, write a `status="success"` trace via `_safe_record(...)`, log `step_succeeded` (`tracing.py:82-88`).
5. **On exception:** compute `duration_ms`, write a `status="failed"` trace carrying `error=str(exc)`, log `step_failed`, and **re-raise** (`tracing.py:73-81`).
6. **`finally`:** `reset_contextvars(**tokens)` to unbind the context, so binding is scoped to the step (`tracing.py:89-90`).

**`_safe_record(**kwargs)` (`tracing.py:47-52`)** wraps `traces_repo.record(**kwargs)` in a try/except that logs `trace_record_failed` and swallows. This enforces the **two hard rules** (`tracing.py:14-18`): (1) a trace-write failure must never break a run; (2) on the failure path the *original* exception always propagates, even if writing the failed row itself errors (the record write is swallowed, then the original `raise` runs).

**Side effects:** one row per call into `agent_traces` (via `traces_repo.record`, `storage/repos/traces.py:12-36` — `INSERT INTO agent_traces (run_id, page_id, agent, step, status, duration_ms, model, tokens, error)` returning the id). Plus structured log lines (`step_started`/`step_succeeded`/`step_failed`). These rows are what `aeo trace <page>` (`traces.for_page`) and run-level observability (`traces.for_run`) read back, oldest-first (`storage/repos/traces.py:39-58`).

#### 10.4.2 Error Sink — `src/aeo/obs/error_sink.py`

Page-level failure isolation, built on the same `traces_repo`. It generalizes the crawler's existing "floor a failed criterion / mark `crawl_status='failed'`" behavior into one helper every per-page block calls: **one bad page never kills a run** (`error_sink.py:1-18`).

**`PageOutcome` (dataclass, `error_sink.py:32-38`):** `failed: bool = False`, `error: str | None = None`. Yielded by `page_guard` so the caller can **skip downstream stages** for a page that failed an earlier one (e.g. `if outcome.failed: continue`).

**`record_failure(agent, error, *, run_id=None, page_id=None, step=None)` (`error_sink.py:49-67`):** notes a failure the caller already caught — writes one `agent="error_sink"`, `status="failed"` trace whose `error` is `f"{agent}: {error}"` (with `step` defaulting to `agent`), and logs `page_skipped`. Use this inside `except` blocks that manage their own control flow but still want the failure on the page's journey.

**`page_guard(agent, *, run_id=None, page_id=None, step=None, reraise=False)` (context manager, `error_sink.py:70-88`):** isolates a unit of per-page work:

```python
with page_guard("analyze", run_id=r, page_id=p) as outcome:
    ...                       # work that might explode
if outcome.failed:
    continue                  # skip the rest of the pipeline for this page
```

On any exception it sets `outcome.failed=True` / `outcome.error=str(exc)`, calls `record_failure(...)` (which writes the `error_sink` trace + logs `page_skipped`), and **by default swallows** the exception so the loop continues. If `reraise=True`, it re-raises after recording. **Successful work writes nothing here** — the inner `trace_step` calls already recorded per-step successes (`error_sink.py:14-18`). Like tracing, its `_safe_record` (`error_sink.py:41-46`) swallows write failures (logs `error_sink_record_failed`), so the sink itself can never break a run.

**Side effects:** at most one `agent_traces` row per failure; structured logs. **Division of labour:** `trace_step` records *per-step* success/failure and re-raises; `page_guard` is the *outer* boundary that catches what propagated and decides whether to abandon the page.

---

### 10.5 Utilities — `src/aeo/utils/`

Small, dependency-light, pure helpers shared across extractors and scorers. None touch the DB or network; none write files.

#### 10.5.1 URL — `src/aeo/utils/url.py`

| Function | Signature | Behavior |
|---|---|---|
| `normalize` | `normalize(url) -> str` | Canonicalize a URL for dedup/comparison. |
| `host_of` | `host_of(url) -> str` | Lowercased hostname (or `""`). |
| `same_site` | `same_site(a, b) -> bool` | True iff both resolve to the same registrable domain (eTLD+1). |
| `absolute` | `absolute(base, href) -> str` | `urljoin(base, href)` — resolve a relative href against a base. |

**`normalize` (`url.py:8-18`)** does, in order: strips surrounding whitespace; **lowercases scheme** (default `"https"` if missing) and **host**; **strips default ports** (80 for http, 443 for https, via `_is_default_port`, `url.py:33-34`) but keeps non-default ports as `host:port`; sets an **empty path to `/`**; **strips a trailing slash** from any non-root path (`/foo/` → `/foo`, but `/` stays `/`); **drops the fragment** (`#...`) entirely; **preserves the query string**. So `HTTPS://Example.com:443/Foo/#frag?` normalizes consistently while `?q=1` is retained.

**`same_site` (`url.py:25-26`)** compares `_registrable(host_of(a))` to `_registrable(host_of(b))`. `_registrable` (`url.py:37-42`) is a **cheap eTLD+1**: it takes the last two dot-labels of the host (`blog.example.com` → `example.com`), returning the whole host if there are fewer than 2 labels. **Trade-off (documented in-code):** accurate for common 2-label TLDs (`.com`, `.io`, `.net`) but **wrong for multi-label public suffixes** like `.co.uk` (it would treat `a.co.uk` and `b.co.uk` as the same site). This is an intentional simplification — no public-suffix-list dependency.

#### 10.5.2 Text — `src/aeo/utils/text.py`

Three precompiled regexes (`text.py:7-9`): `_WS_RE = \s+`, `_WORD_RE = \b[\w\-]+\b` (words include hyphens, so `state-of-the-art` counts as one token), `_SENT_RE = (?<=[.!?])\s+(?=[A-Z0-9])` (split on whitespace that follows `.`/`!`/`?` and precedes an uppercase letter or digit).

| Function | Signature | Behavior |
|---|---|---|
| `collapse_whitespace` | `(text) -> str` | Collapse any run of whitespace to a single space and strip ends. |
| `word_count` | `(text) -> int` | Count `_WORD_RE` matches; `None`/empty → 0. |
| `sentence_split` | `(text) -> list[str]` | Collapse whitespace, then split into trimmed, non-empty sentences (`[]` for empty input). |
| `truncate` | `(text, max_chars, suffix="…") -> str` | If `len(text) <= max_chars` return as-is; else cut to `max_chars - len(suffix)` and append `suffix` (default ellipsis `…`). |

`sentence_split`'s heuristic deliberately won't split on `.` inside e.g. `U.S.` mid-clause unless followed by a capital/digit after whitespace — a pragmatic abbreviation guard.

#### 10.5.3 HTML — `src/aeo/utils/html.py`

Shared BeautifulSoup helpers. **`CHROME_TAGS` (`html.py:8`)** is the vocabulary of "structural chrome, not content" tags stripped before text extraction:

```python
CHROME_TAGS = ("script", "style", "nav", "footer", "header",
               "aside", "noscript", "iframe")
```

| Function | Signature | Behavior |
|---|---|---|
| `parse` | `parse(html) -> BeautifulSoup` | Parse with the fast **lxml** backend; on any lxml error fall back to the stdlib `html.parser`; empty input → empty soup. |
| `body_text` | `body_text(soup) -> str` | **Destructively** strips all `CHROME_TAGS` then returns space-separated, stripped text. |
| `first_text` | `first_text(soup, selector) -> str \| None` | Text of the first CSS-selector match, or `None`. |

**`body_text` is destructive (`html.py:21-25`):** it calls `tag.decompose()` on every chrome tag, **mutating the passed-in soup in place** — `script`/`style`/`nav`/`footer`/`header`/`aside`/`noscript`/`iframe` are permanently removed from that soup object. **Caller caveat:** if you need those tags afterward, re-`parse` or pass a copy. The trade-off is speed/simplicity over immutability: extractors that want plain content text call `body_text` last on a soup they don't reuse. It returns `get_text(separator=" ", strip=True)` — chrome-free, whitespace-joined body text.

#### 10.5.4 Hashing — `src/aeo/utils/hashing.py`

**`content_hash(html, algorithm="sha256") -> str` (`hashing.py:10-14`):** content fingerprint used to **skip recrawls when nothing changed**. It runs the input through `collapse_whitespace` *before* hashing, so **whitespace-only churn (reflows, re-indents) does not change the hash** — only meaningful content edits do. Encodes UTF-8 with `errors="replace"` (never throws on bad bytes), uses `hashlib.new(algorithm)` (default SHA-256), and returns the hex digest. **No side effects** — pure function over the input string.

#### 10.5.5 Timing — `src/aeo/utils/timing.py`

**`stopwatch()` (context manager, `timing.py:10-22`):** a tiny wall-clock timer using `time.perf_counter()` (monotonic, high-resolution — correct even if the system clock changes).

```python
with stopwatch() as t:
    ...
print(t["elapsed_ms"])
```

It yields a mutable dict initialized `{"elapsed_ms": 0.0}` and, in a `finally`, sets `state["elapsed_ms"]` to the elapsed milliseconds — so the value is populated **after** the block exits, even if the body raised. **No side effects.**

---

### 10.6 Cross-cutting notes for the reader

- **Single writer principle.** Only `obs/tracing.py` (via `trace_step`) and `obs/error_sink.py` (via `record_failure`/`page_guard`) write `agent_traces`; only `report/builder.persist_report` writes `page_reports`; site-report writes go through `storage/repos/site_reports.put`. The builder/render modules themselves are pure and DB-free.
- **Failure isolation is layered.** `trace_step` records and *re-raises*; `page_guard` is the outer net that *catches and (by default) swallows*, marking a `PageOutcome` so the per-page loop can skip the rest of the pipeline. Neither writer can crash a run — both wrap their DB write in a swallowing `_safe_record`.
- **The reports are remediation documents.** Both the criterion scores (weakest-first) and the recommendations (gap-priority-first) are ordered for action, and the `human_review` / `review_status` machinery makes "needs a human" a first-class, queryable state rather than a UI affordance.

## 11. Storage Layer — Connection Pool, Migrations, Data Model, Every Repository

The storage layer is a thin, hand-rolled persistence stack: **no ORM**. It is built from three primitives —
a single process-wide psycopg2 connection pool (`db.py`), a tiny file-based migration runner (`migrate.py`),
and plain dataclasses that move typed records between pipeline layers (`models.py`). On top of those sit a
set of focused **repository modules** (`storage/repos/*.py`), each owning the raw SQL for one DB table (or a
small cluster of related tables). The design intent is explicit, auditable SQL with clear transaction
boundaries, and Postgres doing the heavy lifting (queue semantics, generated columns, GIN/JSONB indexes,
advisory locks) rather than an application-layer abstraction.

---

### 11.1 Connection pool & transaction helper — `src/aeo/storage/db.py`

One `psycopg2.pool.ThreadedConnectionPool` per process. It is **Threaded** so that async tasks running in the
default executor each get a fresh connection without contention (`db.py:1-5`).

#### Module-level state
- `_pool: psycopg2.pool.ThreadedConnectionPool | None = None` (`db.py:24`) — lazily-initialized singleton.

#### `_parse_url(url: str) -> dict[str, Any]` — `db.py:27`
Internal. Parses a `DATABASE_URL` via `urllib.parse.urlparse` into the kwargs psycopg2 wants.
- **Validation:** rejects any scheme that is not `postgresql` or `postgres`, raising
  `ValueError("DATABASE_URL must use postgresql:// — got <scheme>")` (`db.py:29-30`).
- **Defaults applied when components are missing:** `host="localhost"`, `port=5432`, `dbname="aeo"`
  (from the URL path, stripped of its leading `/`; falls back to `aeo`), `user="postgres"`, `password=""`
  (`db.py:31-37`).
- **Inputs → outputs:** URL string → `{host, port, dbname, user, password}` dict. No side effects.

#### `get_pool() -> psycopg2.pool.ThreadedConnectionPool` — `db.py:40`
Returns the singleton pool, creating it on first call.
- Reads settings via `get_settings()` and pulls config from the `database` settings block.
- **Config knobs consumed** (from `settings.py`, class `DatabaseCfg` at `settings.py:99-102`):
  | Knob | Settings field | Default | Env override |
  |------|----------------|---------|--------------|
  | Connection string | `s.database.url` | `postgresql://aeo:aeo@localhost:5432/aeo` | `DATABASE_URL` |
  | Min pool connections | `s.database.pool_min` | `2` | `DB_POOL_MIN` |
  | Max pool connections | `s.database.pool_max` | `10` | `DB_POOL_MAX` |

  (`DATABASE_URL`/`DB_POOL_MIN`/`DB_POOL_MAX` are wired in `settings.py:211-218`, preserving the "legacy
  contract" of a single `DATABASE_URL`.)
- **Hard-wired knobs:** `cursor_factory=psycopg2.extras.RealDictCursor` (so every `fetchone`/`fetchall`
  returns dict-like rows, which is why all repos index columns by name like `row["id"]`), and
  `connect_timeout=10` seconds (`db.py:48-50`).
- **Side effect:** emits a structured `db_pool_ready` log with host/db/min/max on creation (`db.py:52-53`).

#### `transaction()` — `db.py:57` (context manager)
```python
@contextlib.contextmanager
def transaction() -> Generator[psycopg2.extensions.connection, None, None]:
```
The single transaction boundary used by **every** repository function. Algorithm (`db.py:59-68`):
1. `conn = pool.getconn()` — borrow a connection.
2. `yield conn` — caller runs its statements (typically `with transaction() as conn, conn.cursor() as cur:`).
3. On clean exit: `conn.commit()`.
4. On **any** exception: `conn.rollback()` then **re-raise** (errors are never swallowed).
5. `finally`: `pool.putconn(conn)` — always returned to the pool, even on error.

This is why repo functions never call commit/rollback themselves — one statement-group per `with` block is one
atomic transaction, and a raised exception rolls the whole group back.

#### `health_check() -> bool` — `db.py:71`
Runs `SELECT 1` inside a `transaction()`. Returns `True` on success; on any exception logs
`db_health_check_failed` with the error string and returns `False` (never raises). Used by readiness checks.

#### `close() -> None` — `db.py:82`
Calls `_pool.closeall()` and resets `_pool = None` (idempotent — no-op if never opened). Logs
`db_pool_closed`. Used at process shutdown.

---

### 11.2 Migration runner — `src/aeo/storage/migrate.py`

A "tiny migration runner": it applies `*.sql` files from `storage/migrations/` **in filename order** and
records each success in a `schema_versions` table. **Idempotent** — re-running only applies files not yet
recorded (`migrate.py:1-5`).

- `MIGRATIONS_DIR = Path(__file__).parent / "migrations"` (`migrate.py:17`).
- `_BOOTSTRAP_SQL` (`migrate.py:20-26`) creates the ledger table if absent:
  ```sql
  CREATE TABLE IF NOT EXISTS schema_versions (
      version     VARCHAR(20)  PRIMARY KEY,
      name        VARCHAR(255) NOT NULL,
      applied_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
  );
  ```

#### `_applied_versions() -> set[str]` — `migrate.py:29`
Internal. Runs the bootstrap DDL, then `SELECT version FROM schema_versions`, returning the set of applied
version strings.

#### `_discover() -> list[tuple[str, str, Path]]` — `migrate.py:36`
Internal. Globs `*.sql`, sorts by filename, and splits each stem on the first `_` into `(version, name)`
(e.g. `0003_jobs.sql` → version `"0003"`, name `"jobs"`). Files whose prefix is **not all digits** are
**skipped with a `migration_skipped` warning** (`migrate.py:41-43`) — the numeric prefix is the ordering and
the dedupe key.

#### `apply_pending() -> list[str]` — `migrate.py:48`
The public entry point.
- Computes `pending = discovered − applied`.
- If none pending: logs `migrations_up_to_date` and returns `[]` (`migrate.py:52-54`).
- Otherwise, for each pending file **in order**: reads the SQL (`utf-8`), executes it, then inserts
  `(version, name)` into `schema_versions` — **all inside one `transaction()` per file** (`migrate.py:57-66`).
  Because each file + its ledger insert share a transaction, a failing migration rolls back cleanly and is
  **not** recorded as applied.
- **Returns** the list of version strings applied this run; logs `migration_applied` per file.
- **Caveat to note for the team lead:** each migration runs in psycopg2's default (autocommit-off) transaction
  but is *not* wrapped in an advisory lock, so concurrent migration runners are not coordinated. In practice
  migrations are run once at deploy time.

---

### 11.3 Typed records — `src/aeo/storage/models.py`

Plain `@dataclass(slots=True)` records (no ORM, no validation logic) that cross layer boundaries. `slots=True`
keeps them lightweight.

| Dataclass | Location | Fields (key ones) | Role |
|-----------|----------|--------------------|------|
| `Target` | `models.py:10` | `id, name, domain, kind` (`kind` = `'client'`\|`'competitor'`) | A client or competitor row |
| `CrawlRun` | `models.py:18` | `id, run_key, label, started_at, status` | A crawl-run row |
| `FetchedPage` | `models.py:27` | `url, url_normalized, success, http_status, fetch_duration_ms, html, markdown, title, meta_description, error, content_hash=None` | Crawler output, pre-persist |
| `StoredPage` | `models.py:42` | `id, url, url_normalized, content_hash, crawl_status` | A persisted `crawled_pages` row (id assigned) |
| `ExtractionBundle` | `models.py:51` | `page_id, data: dict` | Holds per-extractor payloads; `put(name, payload)` / `get(name, default)` helpers (`models.py:56-60`) |
| `CriterionScore` | `models.py:63` | `name, value (1-5), evidence, notes, scored_by="deterministic"` | One rubric criterion's score |
| `PageScore` | `models.py:72` | `page_id, run_id, criteria: dict[str,CriterionScore], total, max_possible, priority_tier, rubric_version="1.0"` | A full page rubric score |

`CriterionScore.scored_by` documents provenance: `'deterministic'` | a model name | `'hybrid'` (`models.py:69`).

---

### 11.4 Data model — every table, walked migration by migration

Schema is built up additively across nine migrations. A reusable trigger function `set_updated_at()`
(`0001_init.sql:4-10`) stamps `NEW.updated_at = NOW()` on `BEFORE UPDATE`; it is attached to `clients` and
`competitors`.

#### Migration `0001_init.sql` — reference tables, runs, pages

**`clients`** (`0001:14`) — the AEO target(s).
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `name` | VARCHAR(255) | `NOT NULL UNIQUE` |
| `domain` | VARCHAR(255) | `NOT NULL UNIQUE` |
| `website_url` | VARCHAR(2048) | `NOT NULL` |
| `industry` | VARCHAR(255) | DEFAULT `'Cybersecurity'` |
| `notes` | TEXT | |
| `is_active` | BOOLEAN | `NOT NULL DEFAULT TRUE` |
| `created_at`/`updated_at` | TIMESTAMPTZ | DEFAULT `NOW()`; `updated_at` maintained by trigger `trg_clients_updated_at` |

**`competitors`** (`0001:33`) — same shape minus `industry`/`notes`, plus `category VARCHAR(255)`. `name` and
`domain` are `NOT NULL UNIQUE`; trigger `trg_competitors_updated_at`.

**`crawl_runs`** (`0001:52`) — first-class run entity (legacy used a bare VARCHAR, comment `0001:51`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `run_key` | VARCHAR(64) | `NOT NULL UNIQUE` |
| `label` | VARCHAR(255) | nullable |
| `started_at` | TIMESTAMPTZ | DEFAULT `NOW()` |
| `finished_at` | TIMESTAMPTZ | nullable |
| `status` | VARCHAR(20) | `NOT NULL DEFAULT 'running'`, **CHECK IN (`running`,`succeeded`,`failed`,`partial`)** |
| `notes` | TEXT | |

**`crawled_pages`** (`0001:65`) — the central page table.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `client_id` | INTEGER | FK→`clients(id)` ON DELETE CASCADE |
| `competitor_id` | INTEGER | FK→`competitors(id)` ON DELETE CASCADE |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs(id)` ON DELETE CASCADE |
| `url` / `url_normalized` | VARCHAR(2048) | both `NOT NULL` |
| `content_hash` | CHAR(64) | sha256 hex; nullable |
| `raw_html`, `markdown_content` | TEXT | |
| `page_title` | VARCHAR(512) | |
| `meta_description` | TEXT | |
| `http_status` | INTEGER | |
| `fetch_duration_ms` | INTEGER | |
| `crawl_status` | VARCHAR(20) | `NOT NULL DEFAULT 'success'`, **CHECK IN (`success`,`failed`,`partial`,`skipped`)** |
| `error_message` | TEXT | |
| `crawled_at`/`created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

Key constraints (the "why"):
- **`chk_single_owner`** (`0001:90-93`): exactly one of `client_id`/`competitor_id` must be set (XOR) — a page
  belongs to a client **or** a competitor, never both/neither.
- **`uq_url_per_run UNIQUE (url_normalized, run_id)`** (`0001:94`): one row per normalized URL per run — this is
  the conflict target the page upsert relies on (§11.6).
- Indexes on `client_id`, `competitor_id`, `run_id`, `url_normalized`, `content_hash`, `crawl_status`, and
  `crawled_at DESC` (`0001:97-103`).

**Seed data** (`0001:106-121`): inserts the `Securin` client (`securin.io`, industry "Cybersecurity /
Preemptive Exposure Validation") and **eight competitors** — SecureLayer7, XM Cyber, AttackIQ, Pentera,
Cymulate, Hive Pro, Picus Security, Ridge Security — each `ON CONFLICT (name) DO NOTHING` (idempotent reseed).

#### Migration `0002_extractions_and_scores.sql` — extractions + rubric scores

**`page_extractions`** (`0002:5`) — one JSONB blob per page.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | `NOT NULL UNIQUE` FK→`crawled_pages(id)` ON DELETE CASCADE — **one extraction row per page** |
| `extracted` | JSONB | `NOT NULL DEFAULT '{}'`; each top-level key is an extractor name (headings, schema_jsonld, qa_blocks…) |
| `extractor_version` | VARCHAR(20) | DEFAULT `'1'` |
| `extracted_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

Indexes: btree on `page_id`, and a **GIN index on `extracted`** (`0002:17`) — enables JSONB containment
queries like "find pages missing FAQ schema" (`extracted->'schema_jsonld'->'types'`).

**`rubric_scores_v2`** (`0002:21`) — originally **8 criteria × 1-5, max 40**.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | `NOT NULL` FK→`crawled_pages(id)` CASCADE |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs(id)` CASCADE |
| `rubric_version` | VARCHAR(20) | `NOT NULL DEFAULT '1.0'` |
| 8 `*_score` columns | SMALLINT | each `NOT NULL CHECK BETWEEN 1 AND 5`: `schema_markup`, `qa_blocks`, `stats_in_html`, `entity_consistency`, `heading_structure`, `content_depth`, `citation_signals`, `load_speed` |
| `total_score` | SMALLINT | `NOT NULL` |
| `max_possible_score` | SMALLINT | `NOT NULL DEFAULT 40` |
| `score_percentage` | NUMERIC(5,2) | **GENERATED ALWAYS AS** `ROUND((total/NULLIF(max,0))*100, 2) STORED` (`0002:40-42`) — DB-computed, never written by app |
| `priority_tier` | VARCHAR(40) | e.g. `'Critical Rework'`, `'High Priority'` |
| `evidence` | JSONB | `NOT NULL DEFAULT '{}'`; per-criterion `{value, evidence, …}` |
| `scored_by` | VARCHAR(100) | provenance |
| `scored_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (page_id, run_id, rubric_version)`** (`0002:50`) — the upsert conflict target. A page can hold
  scores from different runs and different rubric versions simultaneously without collision.
- Indexes on `page_id`, `run_id`, `total_score DESC`.

**`page_score_view`** (`0002:59`) — convenience VIEW joining `crawled_pages` → `clients`/`competitors`
(LEFT JOIN), `crawl_runs` (JOIN), `rubric_scores_v2` (LEFT JOIN). Surfaces `owner_name`
(`COALESCE(competitor.name, client.name)`), `owner_type` (CASE on `client_id`), `run_key`, totals/percentage,
tier, and each criterion score. Recreated by `0008` to add the two v3 criteria.

#### Migration `0003_jobs.sql` — Postgres-as-queue

**`jobs`** (`0003:4`) — DB-backed work queue, "no external broker required" (`0003:1-2`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `kind` | VARCHAR(40) | `NOT NULL`, e.g. `'crawl_batch'` |
| `payload` | JSONB | `NOT NULL` |
| `status` | VARCHAR(20) | `NOT NULL DEFAULT 'pending'`, **CHECK IN (`pending`,`running`,`succeeded`,`failed`,`dead`)** |
| `attempts` | INTEGER | `NOT NULL DEFAULT 0` |
| `max_attempts` | INTEGER | `NOT NULL DEFAULT 4` |
| `run_after` | TIMESTAMPTZ | `NOT NULL DEFAULT NOW()` — supports delayed/backoff scheduling |
| `locked_by` | VARCHAR(120) | worker id holding the claim |
| `locked_at` | TIMESTAMPTZ | |
| `last_error` | TEXT | |
| `created_at`/`updated_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`idx_jobs_ready` is a PARTIAL index** on `(run_after) WHERE status = 'pending'` (`0003:20-22`) — keeps the
  claim query (which orders ready pending jobs) cheap regardless of how many terminal rows pile up. Plus
  plain indexes on `status` and `kind`.
- The claim path uses **`FOR UPDATE SKIP LOCKED`** so concurrent workers don't fight over the same row
  (implemented in `repos/jobs.py`, §11.6). Comment claims it "scales to thousands of jobs/second on a single
  PG instance."

#### Migration `0004_prioritization.sql` — pre-crawl URL ranking

**`page_priorities`** (`0004:6`) — ranking computed **pre-crawl** from discovered URLs, so rows reference
`run_id + url` (not a `crawled_pages.id`, which doesn't exist yet). The top-N where `selected = TRUE` are
processed; the full ranking is persisted for observability ("why was this page skipped?", `0004:1-4`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs(id)` CASCADE |
| `url` | TEXT | `NOT NULL` |
| `page_type` | VARCHAR(40) | `NOT NULL`; one of `homepage|product|solution|pillar|blog|about|contact|utility` |
| `base_weight` | NUMERIC(6,3) | DEFAULT 0; from `page_type` (config `prioritization.yaml`) |
| `traffic_signal` | NUMERIC(10,3) | DEFAULT 0; internal-link count now, GSC export later |
| `final_score` | NUMERIC(12,3) | DEFAULT 0; `base_weight × traffic_signal` |
| `final_rank` | INTEGER | 1 = highest priority; NULL until ranked |
| `selected` | BOOLEAN | `NOT NULL DEFAULT FALSE`; in the top-N cut? |
| `detail` | JSONB | `NOT NULL DEFAULT '{}'`; ranking inputs for observability |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (run_id, url)`** (`0004:20`) — upsert target.
- Indexes: `(run_id)`, `(run_id, final_rank)`, and a **partial** `(run_id) WHERE selected` (`0004:25`) for the
  selected-URLs lookup.

#### Migration `0005_gap_and_recs.sql` — gap analysis + recommendations

**`gap_analyses`** (`0005:6`) — one row per page/run. Dual-layer: `bestpractice_gap` = 60% layer (target −
actual vs Reference Layer targets); `competitor_gap` = 40% layer (vs best competitor page for the query
intent) (`0005:3-5`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | `NOT NULL` FK→`crawled_pages` CASCADE |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs` CASCADE |
| `bestpractice_gap`, `competitor_gap`, `overall_gap` | NUMERIC(6,3) | `NOT NULL DEFAULT 0` |
| `detail` | JSONB | `NOT NULL DEFAULT '{}'`; ordered `criterion_gaps[]` + optional narrative |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (page_id, run_id)`** (`0005:19`); indexes on `page_id`, `run_id`.

**`recommendations`** (`0005:30`) — **append-style log**: multiple rows per page across criteria and Validation
retry attempts, so there is **no natural unique key** (`0005:26-29`). Validation inserts a fresh row per
attempt and updates status/scores by id.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | `NOT NULL` FK→`crawled_pages` CASCADE |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs` CASCADE |
| `rec_type` | VARCHAR(40) | `NOT NULL`; e.g. `'schema'`/`'content'`/`'entity'` (named `rec_type` because `type` is reserved) |
| `criterion` | VARCHAR(40) | rubric criterion addressed; NULL = cross-cutting |
| `payload` | JSONB | `NOT NULL DEFAULT '{}'`; the concrete proposed edit |
| `status` | VARCHAR(20) | `NOT NULL DEFAULT 'proposed'`, **CHECK IN (`proposed`,`validated`,`rejected`,`failed`,`needs_review`)** |
| `attempt` | SMALLINT | `NOT NULL DEFAULT 1`; Validation retry counter (≤3) |
| `validated` | BOOLEAN | `NOT NULL DEFAULT FALSE` |
| `score_before`/`score_after` | NUMERIC(6,3) | rubric total before/after applying the edit on the synthetic page |
| `created_at`/`updated_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- Indexes on `page_id`, `run_id`, `status`.

#### Migration `0006_reports.sql` — per-page deliverable

**`page_reports`** (`0006:4`) — the system's final per-page deliverable. "Human Review" is a `review_status`
flag + a report section, **not a UI** (`0006:1-2`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | `NOT NULL` FK→`crawled_pages` CASCADE |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs` CASCADE |
| `summary` | TEXT | headline narrative |
| `sections` | JSONB | `NOT NULL DEFAULT '{}'`; full body: scores, gaps, recs, validation outcome |
| `review_status` | VARCHAR(20) | `NOT NULL DEFAULT 'pending'`, **CHECK IN (`pending`,`approved`,`rejected`,`could_not_improve`)** |
| `generated_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (page_id, run_id)`** (`0006:17`); indexes on `run_id` and `review_status`.

#### Migration `0007_observability.sql` — agent trace log

**`agent_traces`** (`0007:6`) — one row per agent step per page; written by every stage (Analyze → Gap →
Recommend → Validate → Report) and the Error Sink. Powers `aeo trace <page>`. `run_id`/`page_id` are
**nullable** so run-level or pre-crawl steps can still be traced (`0007:1-4`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `run_id` | INTEGER | FK→`crawl_runs` CASCADE, **nullable** |
| `page_id` | BIGINT | FK→`crawled_pages` CASCADE, **nullable** |
| `agent` | VARCHAR(40) | `NOT NULL`; `analyzer|gap|recommender|validator|reporter|prioritizer` |
| `step` | VARCHAR(60) | finer label within an agent |
| `status` | VARCHAR(20) | `NOT NULL`, **CHECK IN (`started`,`success`,`failed`,`skipped`)** |
| `duration_ms` | INTEGER | |
| `model` | VARCHAR(100) | LLM model (NULL for deterministic steps) |
| `tokens` | INTEGER | prompt+completion, when known |
| `error` | TEXT | populated by Error Sink on failure |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- Indexes: `(page_id, created_at)`, `(run_id, created_at)`, `(agent)` — composite time indexes back the
  ordered-by-time trace dumps.

#### Migration `0008_rubric_v3_10criteria.sql` — rubric 8 → 10 criteria

Expands the rubric to **10 criteria, max 50** (`0008:1`). Additive + idempotent:
- `ADD COLUMN IF NOT EXISTS render_accessibility_score SMALLINT` and
  `answer_readability_score SMALLINT` to `rubric_scores_v2` (`0008:7-13`). Both are **NULLABLE** with
  CHECK `(col IS NULL OR col BETWEEN 1 AND 5)` — so **pre-v3 rows (rubric_version '1.0', 8 criteria) remain
  valid** with NULL in the two new columns.
- `ALTER COLUMN max_possible_score SET DEFAULT 50` (`0008:16`) — new scores carry the v3 max; existing rows
  keep their stored 40.
- **DROP + recreate `page_score_view`** (`0008:22-52`) — `CREATE OR REPLACE` can't insert columns mid-list, so
  the view is dropped and rebuilt to keep the two new score columns grouped in rubric order.

#### Migration `0009_v4_reference_architecture.sql` — the v4 Reference Architecture

Additive and idempotent — no v3 table semantics changed (`0009:1-4`).

**`blueprints`** (`0009:11`) — the versioned, per-topic "ideal site".
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `topic` | VARCHAR(120) | `NOT NULL` |
| `version` | INTEGER | `NOT NULL` |
| `generator` | VARCHAR(80) | `NOT NULL DEFAULT 'deterministic'` |
| `framework_version` | VARCHAR(40) | `NOT NULL DEFAULT '0'` |
| `content_hash` | CHAR(64) | `NOT NULL`; hash of blueprint **inputs** (topic + framework version + competitors + structure) |
| `competitors` | JSONB | `NOT NULL DEFAULT '[]'` |
| `body` | JSONB | `NOT NULL DEFAULT '{}'`; full Blueprint JSON (`Blueprint.to_jsonb()`) |
| `notes` | TEXT | |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (topic, version)`** (`0009:23`) — this is the constraint protected by the **per-topic advisory
  lock** in `repos/blueprints.py` (§11.6). Identical inputs reuse a version; any structural change bumps it.
- Indexes: `(topic, version DESC)`, `(topic, content_hash)`.
- **`crawl_runs.blueprint_id`** column added (`0009:32-33`): nullable FK→`blueprints(id)`. Pins each run to the
  blueprint version it was measured against, so a score jump reads as "new baseline" vs "real change". Nullable
  because pre-v4 runs / generator-disabled runs have no pinned blueprint.

**`coverage_diffs`** (`0009:38`) — site-level gap (discovered sitemap vs ideal sitemap), one row per run.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs` CASCADE |
| `blueprint_id` | BIGINT | FK→`blueprints(id)` **ON DELETE SET NULL** |
| `target_id` | INTEGER | the client target — **deliberately FK-free** (targets is seed data; comment `0009:43`) |
| `coverage_pct` | NUMERIC(5,1) | `NOT NULL DEFAULT 0` |
| `missing_count`, `thin_count` | INTEGER | `NOT NULL DEFAULT 0` |
| `detail` | JSONB | `NOT NULL DEFAULT '{}'`; missing/thin/matched node lists |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (run_id)`** (`0009:52`) — note `0009:50-51`: this UNIQUE already creates the btree index for
  `run_id` lookups, so no separate single-column index is declared (it would be pure write overhead).

**`citation_results`** (`0009:59`) — the validated-wins real-world signal: did a page (or its proposed
rewrite) actually get cited for its target question? Feeds criteria-refinement proposals.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `page_id` | BIGINT | FK→`crawled_pages` CASCADE, nullable |
| `run_id` | INTEGER | FK→`crawl_runs` CASCADE, nullable |
| `url` | TEXT | `NOT NULL` |
| `question` | TEXT | `NOT NULL` |
| `cited` | BOOLEAN | `NOT NULL DEFAULT FALSE` |
| `engine` | VARCHAR(40) | `NOT NULL DEFAULT 'perplexity'` |
| `evidence` | JSONB | `NOT NULL DEFAULT '{}'` |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- Indexes on `page_id`, `run_id`, `cited`.

**`criteria_refinements`** (`0009:82`) — controlled, **human-gated** learning. Cited pages propose nudges to
the Reference-Layer criteria *definitions*. The system **NEVER auto-applies** — "that would be circular
validation one level up" (`0009:78-81`).
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `criterion` | VARCHAR(40) | `NOT NULL` |
| `current_target`, `proposed_target` | SMALLINT | nullable |
| `rationale` | TEXT | `NOT NULL` |
| `evidence` | JSONB | `NOT NULL DEFAULT '{}'` |
| `status` | VARCHAR(20) | `NOT NULL DEFAULT 'proposed'`, **CHECK IN (`proposed`,`accepted`,`rejected`)** |
| `created_at`/`updated_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- Index on `status`.

**`site_reports`** (`0009:99`) — the site-level deliverable (coverage + per-page rollup), one row per run.
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `run_id` | INTEGER | `NOT NULL` FK→`crawl_runs` CASCADE |
| `target_id` | INTEGER | FK-free |
| `blueprint_id` | BIGINT | FK→`blueprints(id)` ON DELETE SET NULL |
| `summary` | TEXT | |
| `sections` | JSONB | `NOT NULL DEFAULT '{}'` |
| `created_at` | TIMESTAMPTZ | DEFAULT `NOW()` |

- **`UNIQUE (run_id)`** (`0009:110`) — again doubles as the lookup index.

---

### 11.5 Cross-cutting repo conventions

Every repo function follows the same idioms, so they are stated once here:
- All DB access goes through `with transaction() as conn, conn.cursor() as cur:` — one `with` block = one
  committed transaction; an exception rolls back and propagates.
- Rows come back as dict-like (`RealDictCursor`), so columns are accessed by name.
- JSONB writes use `json.dumps(payload, default=str)` and a `%s::jsonb` cast — `default=str` lets non-JSON
  types (datetimes, Decimals) serialize without error.
- Upserts use `INSERT … ON CONFLICT (<unique key>) DO UPDATE … RETURNING id`, so writes are idempotent against
  each table's natural unique key.

---

### 11.6 Repositories — `src/aeo/storage/repos/*.py`

#### `targets.py` — clients & competitors
- `by_name(name, kind)` → `Target | None` (`targets.py:13`): selects `id, name, domain` from `clients` or
  `competitors` (table chosen by `kind`).
- `find(name)` → `Target | None` (`targets.py:23`): tries client, then competitor.
- `list_all(kind, active_only=True)` → `list[Target]` (`targets.py:28`): lists all of one kind, optionally
  `WHERE is_active = TRUE`, ordered by name.

#### `runs.py` — crawl-run lifecycle
- `new_run_key()` → `str` (`runs.py:12`): generates `run_<UTC yyyymmdd_HHMMSS>_<6 hex>`.
- `start(label=None, run_key=None)` → `CrawlRun` (`runs.py:17`): `INSERT INTO crawl_runs (run_key, label)
  … RETURNING …`; auto-generates a key if none given. Side effect: a new run row (status defaults to
  `running`).
- `finish(run_id, status="succeeded", notes=None)` (`runs.py:32`): `UPDATE … SET finished_at = NOW(), status,
  notes`.
- `get(run_id)` → `CrawlRun | None` (`runs.py:40`).

#### `jobs.py` — Postgres-as-queue
- `enqueue(kind, payload, run_after=None, max_attempts=4)` → `int` (`jobs.py:17`): inserts a job; `run_after`
  defaults to `NOW()` via `COALESCE`. Returns the job id.
- `claim(worker_id, kinds=None)` → `dict | None` (`jobs.py:31`): **the atomic claim**. A CTE selects the next
  ready job (`status='pending' AND run_after <= NOW()`, optional `kind = ANY(%s)` filter), `ORDER BY id`,
  **`FOR UPDATE SKIP LOCKED LIMIT 1`**, then the outer `UPDATE` flips it to `running`, sets `locked_by`/
  `locked_at`, increments `attempts`, and `RETURNING j.*`. SKIP LOCKED means concurrent workers each grab a
  different row without blocking.
- `succeed(job_id)` (`jobs.py:63`): sets `status='succeeded'`, clears `locked_by`.
- `fail(job_id, error, backoff_sec=30)` (`jobs.py:72`): **retry-or-dead** — `status = CASE WHEN attempts >=
  max_attempts THEN 'dead' ELSE 'pending' END`, pushes `run_after` out by `backoff_sec` seconds, records
  `last_error`, clears `locked_by`. So a failing job is requeued with backoff until it exhausts
  `max_attempts`, then parked as `dead` for manual inspection.
- `stats()` → `dict[str,int]` (`jobs.py:93`): counts by `status`.

#### `pages.py` — crawled_pages
- `upsert(page: FetchedPage, run_id, client_id, competitor_id)` → `StoredPage` (`pages.py:35`): big upsert
  (`_UPSERT`, `pages.py:8-32`) on conflict target **`(url_normalized, run_id)`**; updates content/HTML/status
  and refreshes `crawled_at = NOW()`. `crawl_status` is set to `'success'` or `'failed'` from `page.success`;
  `page_title` is truncated to 512 chars to fit the column.
- `last_hash(url_normalized)` → `str | None` (`pages.py:66`): most recent non-null `content_hash` for a URL
  across **any** run (drives the unchanged-content short-circuit).
- `get(page_id)` → `dict | None` (`pages.py:79`): `SELECT *`.
- `by_run(run_id)` → `list[dict]` (`pages.py:85`): all pages for a run, ordered by id.

#### `extractions.py` — page_extractions
- `put(bundle: ExtractionBundle, extractor_version="1")` → `int` (`extractions.py:11`): upsert on `(page_id)`;
  stores `bundle.data` as JSONB, refreshes `extracted_at`.
- `copy_latest_for_url(url_normalized, new_page_id)` → `bool` (`extractions.py:28`): **fingerprint
  short-circuit** — `INSERT … SELECT` clones the most recent prior extraction for the same URL (any other page)
  onto `new_page_id`, `ON CONFLICT (page_id) DO NOTHING`. Returns whether a row was copied. Avoids re-running
  extractors when content is unchanged.
- `get(page_id)` → `ExtractionBundle | None` (`extractions.py:50`): rebuilds the bundle from `extracted`.

#### `scores.py` — rubric_scores_v2 (10-criterion v3)
- `_tier(score, name)` → `int | None` (`scores.py:11`): returns a criterion's value, or `None` when absent —
  so a pre-v3 (8-criterion) score still inserts cleanly into the nullable v3 columns.
- `put(score: PageScore, scored_by)` → `int` (`scores.py:18`): upsert on **`(page_id, run_id,
  rubric_version)`**; writes all 10 `*_score` columns (the two v3 ones may be NULL), totals, tier, and an
  `evidence` JSONB built per-criterion as `{value, notes, **evidence}`. Refreshes `scored_at`. Note: it does
  **not** write `max_possible_score` on update — the DB default (50) applies on insert and existing rows keep
  their value.
- `copy_latest_for_url(url_normalized, new_page_id, new_run_id)` → `bool` (`scores.py:80`): pairs with the
  extraction short-circuit — `INSERT … SELECT` clones the latest prior score for the URL onto the new
  page/run, `ON CONFLICT DO NOTHING`.
- `run_report(run_id)` → `dict` (`scores.py:114`): three queries — aggregate (count, avg/min/max total, avg
  pct), tier distribution (`GROUP BY priority_tier`), and the 10 **worst** pages (`ORDER BY total_score ASC
  LIMIT 10`). Backs `aeo status`.
- `_TIER_COLS` (`scores.py:152-157`): the canonical comma-joined list of the 10 score columns + `total_score`
  — a **single source of truth** reused by `latest_competitor_scores` and by `feedback.recent_observations`.
- `latest_competitor_scores(limit=500)` → `list[dict]` (`scores.py:182`): `DISTINCT ON (page_id)` most-recent
  score per competitor page (across all runs), with per-criterion tiers — the candidate pool for the gap
  analysis's 40% competitor layer.
- `pages_pending_score(run_id, limit=50)` → `list[int]` (`scores.py:200`): page ids in the run that have an
  extraction (`JOIN page_extractions`) and `crawl_status='success'` but **no** score for that run
  (`LEFT JOIN … WHERE s.id IS NULL`).
- `scored_pages_for_run(run_id, *, owner=None, limit=100_000)` → `list[dict]` (`scores.py:160`): `(page_id,
  url)` for every scored page in a run, optionally filtered to client- or competitor-owned. Drives the
  standalone analysis phase.

#### `priorities.py` — page_priorities
- `upsert(run_id, url, page_type, base_weight, traffic_signal, final_score, *, final_rank=None,
  selected=False, detail=None)` → `int` (`priorities.py:11`): upsert on **`(run_id, url)`**.
- `selected_urls(run_id)` → `list[str]` (`priorities.py:50`): the top-N `selected = TRUE` URLs, ordered
  `final_rank NULLS LAST, final_score DESC`.
- `ranking(run_id)` → `list[dict]` (`priorities.py:61`): the **full** ranking incl. non-selected URLs (the
  "why was it skipped?" observability view).

#### `gaps.py` — gap_analyses
- `put(page_id, run_id, bestpractice_gap, competitor_gap, overall_gap, detail=None)` → `int` (`gaps.py:11`):
  upsert on **`(page_id, run_id)`**; refreshes `created_at` on update.
- `get(page_id, run_id)` → `dict | None` (`gaps.py:42`).

#### `recommendations.py` — recommendations (append log)
- `create(page_id, run_id, rec_type, payload, *, criterion=None, attempt=1, status="proposed",
  score_before=None)` → `int` (`recommendations.py:16`): **plain INSERT** (no upsert — append-style), returns
  the row id for later validation.
- `set_validation(rec_id, *, status, validated, score_after=None)` (`recommendations.py:46`): updates a row's
  validation outcome by id; refreshes `updated_at`.
- `for_page(page_id, run_id)` → `list[dict]` (`recommendations.py:65`): all recs for a page/run, oldest first
  (attempt order).

#### `reports.py` — page_reports
- `put(page_id, run_id, summary, sections, *, review_status="pending")` → `int` (`reports.py:11`): upsert on
  **`(page_id, run_id)`**; refreshes `generated_at`.
- `get(page_id, run_id)` → `dict | None` (`reports.py:40`).
- `set_review_status(report_id, review_status)` (`reports.py:51`): flips the Human-Review flag.
- `pending_review(run_id)` → `list[dict]` (`reports.py:60`): the review queue — reports with
  `review_status IN ('pending','could_not_improve')`.
- `for_page(page_id)` → `list[dict]` (`reports.py:75`): most recent report for one page (0 or 1).
- `for_run(run_id)` → `list[dict]` (`reports.py:92`): every report for a run, **worst review state first**
  (`could_not_improve` then `pending` then by URL).
- `for_target(target_id, kind, *, run_id=None)` → `list[dict]` (`reports.py:109`): reports owned by a
  client/competitor; when `run_id` is omitted it `COALESCE`s to that target's **most recent** report-producing
  run via a `MAX(run_id)` subquery. Same worst-first ordering.

#### `traces.py` — agent_traces
- `record(agent, status, *, run_id=None, page_id=None, step=None, duration_ms=None, model=None, tokens=None,
  error=None)` → `int` (`traces.py:12`): single INSERT, returns id. Called once per step by every stage and the
  Error Sink.
- `for_page(page_id)` → `list[dict]` (`traces.py:39`): a page's full journey, oldest first — backs
  `aeo trace <page>`.
- `for_run(run_id)` → `list[dict]` (`traces.py:50`): all traces for a run, oldest first.

#### `feedback.py` — citation_results + criteria_refinements
SQL only; the proposal *logic* lives in `reference.feedback` (`feedback.py:1-7`).
- `_VALID_REFINEMENT_STATUSES = {"proposed","accepted","rejected"}` and `_check_status` (`feedback.py:19-28`):
  validates status **in Python** so a bad value raises a clear `ValueError` instead of a raw Postgres CHECK
  violation — mirroring the migration-0009 constraint.
- `record_citation(*, page_id, run_id, url, question, cited, engine="perplexity", evidence=None)` → `int`
  (`feedback.py:34`): plain INSERT into `citation_results`.
- **`recent_observations(limit=500)`** → `list[CitationObservation]` (`feedback.py:56`): the v4
  run-correlation query. `DISTINCT ON (cr.page_id)` returns the most-recent citation outcome per page, joined
  to its rubric tiers. **Crucially it uses a `LEFT JOIN rubric_scores_v2 s ON s.page_id = cr.page_id AND
  s.run_id = cr.run_id`** (`feedback.py:74-76`) — the score is correlated to the **same run** as the citation,
  so a page re-scored in a later run (or under a different `rubric_version`) is **not** judged against an
  unrelated run's tiers. The LEFT (not INNER) JOIN is deliberate: citations whose page has no matching-run
  score still surface, but with empty `tiers`; the downstream proposer skips them per-criterion
  (`if criterion in o.tiers`) rather than the whole page vanishing as an INNER JOIN would cause. Tier column
  names are derived from `scores._TIER_COLS` (single source of truth), stripping the `_score` suffix and
  excluding `total_score`; each present tier is coerced to `int`.
- `save_refinement(ref: CriteriaRefinement)` → `int` (`feedback.py:95`): validates status, then INSERTs a
  proposal.
- `list_refinements(status=None)` → `list[dict]` (`feedback.py:113`): optionally filtered by status, newest
  first.
- `set_refinement_status(refinement_id, status)` (`feedback.py:124`): validates and updates status; refreshes
  `updated_at`. This is the human accept/reject gate.

#### `blueprints.py` — blueprints (advisory-lock-protected versioning)
Round-trips `aeo.reference.blueprint.Blueprint` through the `body` JSONB column. Defines a small
`StoredBlueprint(id, blueprint, reused)` dataclass (`blueprints.py:23`).
- **`save_versioned(blueprint)`** → `StoredBlueprint` (`blueprints.py:34`): the reuse-vs-bump core. Algorithm:
  1. `bp = blueprint.with_hash()` — compute the content hash of the inputs.
  2. **`SELECT pg_advisory_xact_lock(hashtext('aeo:blueprint:' || topic))`** (`blueprints.py:46`) — a
     **per-topic transaction-scoped advisory lock**, auto-released on commit/rollback. This is the key design
     decision: the bump reads `MAX(version)+1` then INSERTs, so two concurrent same-topic generations would
     otherwise read the same max and collide on `UNIQUE(topic, version)`. The advisory lock serializes them so
     the monotonic-version semantics hold **without** an error-and-retry path.
  3. If a row with the same `(topic, content_hash)` exists → return it with `reused=True` (no new version).
  4. Else compute `next = COALESCE(MAX(version),0)+1`, `model_copy(update={version: next})`, INSERT, return
     `reused=False`.
- `latest(topic)` → `StoredBlueprint | None` (`blueprints.py:84`): highest-version blueprint for a topic.
- `get(blueprint_id)` → `StoredBlueprint | None` (`blueprints.py:95`).
- `by_version(topic, version)` → `StoredBlueprint | None` (`blueprints.py:102`).
- `pin_run(run_id, blueprint_id)` (`blueprints.py:112`): `UPDATE crawl_runs SET blueprint_id` — records which
  blueprint version a run was measured against (so a score jump = "new baseline" vs "real change").

#### `coverage.py` — coverage_diffs
- `put(run_id, *, blueprint_id, target_id, coverage_pct, missing_count, thin_count, detail)` → `int`
  (`coverage.py:10`): upsert on **`(run_id)`**. Note the explicit comment (`coverage.py:35-36`): `created_at` is
  **left untouched** on refresh — it is the first-insert time for this run's coverage, not a last-written
  timestamp.
- `get(run_id)` → `dict | None` (`coverage.py:47`).

#### `site_reports.py` — site_reports
Round-trips `aeo.report.site_builder.SiteReport`.
- `put(report: SiteReport)` → `int` (`site_reports.py:11`): upsert on **`(run_id)`**; like coverage, leaves
  `created_at` untouched on refresh (`site_reports.py:23-24`).
- `for_run(run_id)` → `dict | None` (`site_reports.py:35`).

## 12. Pipeline Orchestration & Configuration Files

This section documents the *conductor* of the AEO Crawler — the package that wires every other component into two end-to-end phases (crawl/score and analyze/report), plus the queue worker that lets the same logic scale horizontally. It then gives a key-by-key reference for every `config/*.yaml` file.

The whole package lives under `src/aeo/pipeline/`. Its public surface is re-exported from `pipeline/__init__.py:4` so callers import from one place:

```python
from .analysis import AnalysisResult, analyze_page, build_competitor_pool, is_could_not_improve, is_improved
from .orchestrator import AnalysisSummary, Orchestrator, RunSummary
from .stages import ExtractStage, PersistStage, ScoreStage
from .worker import ANALYZE_RUN, CRAWL_BATCH, Worker, enqueue_analysis, enqueue_batch
```

### Design intent: why an async Orchestrator and NOT LangGraph

This is a deliberate, documented architectural decision (recorded in the project's V3→V4 migration notes). The team evaluated adopting a graph-execution framework (LangGraph) for the multi-step agent pipeline and chose to **keep the hand-written async orchestrator**. The rationale that shows up directly in the code:

- The pipeline is a **linear, deterministic-first** chain (crawl → extract → score → gap → validate → report). There are no dynamic branching graphs or cyclic agent hand-offs that a graph engine would buy you; the only "loop" is the validation retry (`max_attempts: 3`) which lives inside the validator, not in the orchestration layer.
- **Isolation is per-page, by an Error Sink** (`page_guard` / `trace_step`), not by a framework supervisor. One bad page is recorded and skipped; the run continues. See `_analyze_one` (`orchestrator.py:263`) and the `with page_guard(...) as outcome` pattern.
- **Concurrency is plain `asyncio` + `ThreadPoolExecutor`** with explicit semaphores, which the team can reason about and unit-test directly. Each stage is a tiny single-responsibility class (`stages.py`) that the CLI path and the queue worker reuse byte-for-byte.
- The queue is **Postgres-backed (`FOR UPDATE SKIP LOCKED`)**, so horizontal scaling needs no external broker and no graph runtime. Adopting LangGraph would have added a dependency and an execution model without removing any real complexity.

Net: the orchestrator is ~440 lines of explicit Python that a team lead can read top to bottom, which is exactly the trade-off chosen over a heavier framework.

---

### `pipeline/orchestrator.py` — the Orchestrator class

The Orchestrator owns a *run* across both phases. The module docstring (`orchestrator.py:1`) lays out the two phases:

- **Crawl/score** (`run_urls`): start run → crawl (async, browser reused) → batch PageSpeed (async) → per page: persist → fingerprint short-circuit OR extract+score+persist → finish run.
- **Analysis** (`analyze_run`): per scored client page → Gap → Validate (recommend + simulate + retry ≤3) → Report, each page Error-Sink isolated.

Module constant: `_PSI_MAX_CONCURRENCY = 5` (`orchestrator.py:51`) — hard ceiling on concurrent PageSpeed Insights calls regardless of crawler concurrency.

#### Result dataclasses

| Class | Fields | Purpose |
|---|---|---|
| `RunSummary` (`orchestrator.py:54`) | `run_id, run_key, total, extracted, scored, unchanged, failed` | Tally of a crawl/score run. `.as_dict()` → JSON-able dict. |
| `AnalysisSummary` (`orchestrator.py:76`) | `run_id, total, analyzed, improved, could_not_improve, failed` | Tally of an analysis run. `.as_dict()` → JSON-able dict. |

Both are `@dataclass(slots=True)`.

#### `Orchestrator.__init__(self, llm: LLMClient | None = None)` — `orchestrator.py:97`

Builds the three reusable stages and resolves the LLM client once:

```python
self._llm    = llm or get_client()      # nlp.llm.get_client()
self.extract = ExtractStage()
self.score   = ScoreStage(self._llm)
self.persist = PersistStage()
```

**Side effect:** none yet (no DB/network until a `run_*` call). The LLM client is shared by the score stage and the analysis phase.

#### `async run_urls(urls, *, target, label=None, do_score=True) -> RunSummary` — `orchestrator.py:103`

Crawl + extract (+ optionally score) an **explicit URL list**.

- **Inputs:** iterable of URLs; a `Target` (client or competitor); optional run `label`; `do_score` flag.
- **`do_score=False`** stops after extraction so scoring can run later as the separate `aeo score` phase.
- **Side effects:** `runs_repo.start(label=...)` creates the `runs` row, then delegates to `_run_pages`.
- **Output:** `RunSummary`.

#### `async run_site(domain, *, target, label=None, do_score=True, max_urls=None) -> RunSummary` — `orchestrator.py:116`

The front of the v3/v4 Crawler block as one call: **Site Discovery → Page Prioritization → crawl/extract(+score) the top-N**, plus the v4 Reference-Architecture side-block.

Algorithm:
1. `cfg = load_prioritization_cfg()` — loads `config/prioritization.yaml`.
2. `discovery = await discover(domain, max_urls=max_urls)` — sitemap-first, recursive-BFS fallback (`config/crawler.yaml → discovery`).
3. `scored = prioritize([PageInput(d.url, d.internal_links) ...], cfg)` — ranks the *full* inventory.
4. `run = runs_repo.start(label=label or f"site:{domain}")` and `persist_ranking(run.id, scored)` — the **full** ranking is persisted for observability, not just the selected pages.
5. `selected = [s.url for s in scored if s.selected]` — only the top-N (`prioritization.top_n`) carry through.
6. Logs `site_discovered` with `source`, `discovered`, `selected` counts.

**Isolated Reference-Architecture block (`orchestrator.py:150-157`)** — this is wrapped in its own `try/except` on purpose:
```python
try:
    stored_bp = generate_and_pin_blueprint(run.id, llm=self._llm)
    if stored_bp is not None:
        compute_and_persist_coverage(run.id, stored_bp, scored,
                                     target_id=target.id, reference=load_reference())
except Exception as exc:
    log.warning("reference_architecture_skipped", run_key=run.run_key, error=str(exc))
```
Design intent: a generator/DB hiccup (e.g. no competitor data yet) **logs `reference_architecture_skipped` and is skipped** — it must never abort the crawl that follows. Note the Coverage Diff runs over the **full discovered inventory** (`scored`), not the crawled top-N.

7. If `not selected` (discovery found nothing), `runs_repo.finish(run.id, status="succeeded")` and return an empty `RunSummary`.
8. Otherwise delegate to `_run_pages(selected, ...)`.

#### `async _run_pages(urls, *, run, target, do_score) -> RunSummary` — `orchestrator.py:165`

Shared inner loop for both `run_urls` and `run_site`. Crawl → extract → (score) every URL into one run, isolated per page.

- `client_id, competitor_id = _owner_ids(target)` — `crawled_pages` enforces exactly one owner (DB check `chk_single_owner`); `_owner_ids` (`orchestrator.py:414`) returns `(target.id, None)` for a client target, `(None, target.id)` otherwise.
- `pages = await fetch_many(urls)` — the async crawler with the browser reused across URLs; sets `summary.total`.
- **PageSpeed only when scoring:** `if do_score: psi_map = await self._psi_batch(...)` over the successful URLs only.
- Per page: `self._process_one(...)`.
- **Run finalization:** `status = "succeeded" if summary.failed == 0 else "partial"`; `runs_repo.finish(run.id, status=status)`. On any exception the run is closed `status="failed"` with `notes=str(exc)` and the exception re-raised.
- **DB tables touched:** `runs` (finish), plus everything `_process_one` writes.

#### `score_run(self, run_id, limit=100_000) -> int` — `orchestrator.py:202`

Synchronous. Scores every **extracted-but-unscored** page in a run; powers the standalone `aeo score` phase and re-scoring after a crawl-only run.

- `pending = scores_repo.pages_pending_score(run_id, limit=limit)`.
- For each `page_id`: `bundle = extractions_repo.get(page_id)` (skip if `None`), `page_score = self.score.run(bundle, run_id)`, `self.persist.score(page_score, scored_by=_scored_by(page_score))`.
- Logs `score_run_complete`; returns count scored.

#### `analyze_run(self, run_id, *, persist=True, limit=100_000) -> AnalysisSummary` — `orchestrator.py:217`

The back half of the pipeline (**Gap → Validate → Independent-Validate → Report**) for every scored **client** page in a run.

Setup loads everything once: `load_reference()`, `load_rubric()`, `load_prioritization_cfg()`, and a **competitor pool** built from `scores_repo.latest_competitor_scores()` via `build_competitor_pool` (so competitor bundles are never reloaded — see analysis.py below).

Two config knobs read from `get_settings().validation`:
- `independent = settings.validation.independent_enabled` — env `AEO__VALIDATION__INDEPENDENT_ENABLED` (default **True**). Turns on the v4 Independent Validator.
- `concurrency = max(1, settings.validation.analysis_concurrency)` — env `AEO__VALIDATION__ANALYSIS_CONCURRENCY` (default **1**).

Pages come from `scores_repo.scored_pages_for_run(run_id, owner="client", limit=limit)`.

**Independent-validation + parallel fan-out (`orchestrator.py:242-252`):**
```python
def work(row): return self._analyze_one(row, ...)
if concurrency > 1 and len(pages) > 1:
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="analysis") as ex:
        results = list(ex.map(work, pages))
else:
    results = [work(row) for row in pages]
```
Design intent (docstring): each page is independent and Error-Sink isolated, so fanning them out across a thread pool is safe and **order-independent — the tally is identical** to the sequential path. `concurrency=1` is the v3 behavior; the real win comes when an LLM is enabled (the per-page validate step is I/O-bound). This is the "v4 Parallel Processor at the analysis tier."

Results are summed into `AnalysisSummary` (`analyzed`, `improved`, `could_not_improve`, `failed`); logs `analyze_run_complete`.

#### `_analyze_one(self, row, *, run_id, reference, rubric, cfg, pool, perplexity, independent, persist) -> dict[str, bool]` — `orchestrator.py:263`

Analyzes a single scored page **inside the Error Sink** so one failure can't abort the run. Returns the per-page tally contribution so the loop works identically sequentially or pooled.

```python
out = {"analyzed": False, "improved": False, "cni": False, "failed": False}
with page_guard("analysis", run_id=run_id, page_id=page_id) as outcome:
    bundle = extractions_repo.get(page_id)
    if bundle is None: return out
    page_score = self.score.run(bundle, run_id)
    result = analyze_page(bundle=bundle, score=page_score, url=url,
                          reference=reference, rubric=rubric, llm=self._llm,
                          competitors=pool, page_type=classify_page_type(url, cfg),
                          persist=persist, perplexity=perplexity, independent=independent)
    out["analyzed"] = True
    out["improved"] = is_improved(result)
    out["cni"]      = is_could_not_improve(result)
if outcome.failed: out["failed"] = True
return out
```
Note `page_type` is computed by `classify_page_type(url, cfg)` (the prioritizer's classifier) and the score is **recomputed from the bundle** rather than reloaded — cheap and keeps the analysis self-contained.

#### `async audit_cycle(domain, *, target, label=None, max_urls=None) -> dict` — `orchestrator.py:295`

The **v4 Weekly Audit Loop** entrypoint, designed to be invoked weekly by a systemd timer / cron (see `ops/`). Full chain:

1. `run_summary = await self.run_site(domain, ..., do_score=True, max_urls=max_urls)` — discovery → prioritization → blueprint → coverage diff → crawl/score (content-hash gated).
2. `analysis = self.analyze_run(run_summary.run_id)` — Gap → Validate → Independent-Validate → per-page report.
3. `site_report_id = self._build_and_persist_site_report(run_summary.run_id, target)` — the site-level roll-up.
4. Logs `audit_cycle_complete`; returns `{"run": ..., "analysis": ..., "site_report_id": ...}`.

#### `_build_and_persist_site_report(self, run_id, target) -> int | None` — `orchestrator.py:323`

Assembles + persists the site-level report from the run's coverage diff, pinned blueprint, and per-page reports. Imports are **local** to keep the module's import graph light.

- `cov_row = coverage_repo.get(run_id)` — returns `None` (no site report) if there's no coverage row or no `blueprint_id`.
- `stored = blueprints_repo.get(cov_row["blueprint_id"])` — `None` → return `None`.
- `coverage = CoverageDiffResult.from_detail(cov_row.get("detail") or {})` — rehydrates the diff.
- Builds a `pages` list from `reports_repo.for_run(run_id)`, pulling each report's `overview` section (`url`, `total`, `max_possible`, `priority_tier`) and `review_status`.
- `site = build_site_report(blueprint=stored.blueprint, coverage=coverage, pages=pages, run_id=run_id, target_id=target.id, blueprint_id=stored.id)`.
- `return site_reports_repo.put(site)`.
- **DB tables touched (read):** `coverage`, `blueprints`, `reports`; **(write):** `site_reports`.

#### `_process_one(self, page, run_id, client_id, competitor_id, psi_map, summary, do_score)` — `orchestrator.py:361`

The per-page worker for the crawl/score phase, containing the **fingerprint short-circuit**.

1. **Failed fetch:** persist the page row (so the failure is recorded), `summary.failed += 1`, return.
2. **Fingerprint short-circuit (only when `do_score`):**
   ```python
   if do_score and fingerprint.should_skip(page.url_normalized, page.content_hash):
       stored = self.persist.page(...)
       if self.persist.copy_unchanged(page.url_normalized, stored.id, run_id):
           summary.unchanged += 1; log.info("unchanged_skip", url=page.url); return
   else:
       stored = self.persist.page(...)
   ```
   Design intent (module docstring, `orchestrator.py:14`): the fingerprint check runs **before** the page is upserted for *this* run so it compares against **prior runs only** (otherwise it would always match the row just written). On an unchanged page the prior extraction + score are **cloned forward** (`copy_unchanged`), skipping the expensive extract + LLM work. The short-circuit only pays off when scoring (it's what skips the LLM); for crawl-only runs extraction is cheap, so it's just redone.
3. **Extract + persist:** `psi = psi_map.get(page.url_normalized) if do_score else None`; `bundle = self.extract.run(page, stored.id, psi)`; `self.persist.extraction(bundle)`; `summary.extracted += 1`.
4. **Score (if `do_score`):** `page_score = self.score.run(bundle, run_id)`; `self.persist.score(page_score, scored_by=_scored_by(page_score))`; `summary.scored += 1`.

#### `async _psi_batch(self, urls) -> dict[str, dict]` — `orchestrator.py:397`

Fetches PageSpeed Insights for many URLs concurrently, keyed by normalized URL.

- `if not s.psi_api_key: return {}` — env `AEO__PSI_API_KEY` (default `None`). With no key the `load_speed` scorer falls back to a neutral score.
- Concurrency: `sem = asyncio.Semaphore(min(_PSI_MAX_CONCURRENCY, max(1, s.crawler.concurrency)))` → at most **5** (or crawler concurrency if lower).
- `await asyncio.gather(...)` over each URL; drops any URL whose fetch returned `None`.
- **Network calls:** Google PageSpeed Insights API per URL.

#### Module helpers

- `_owner_ids(target)` (`orchestrator.py:414`) — single-owner mapping (above).
- `_scored_by(page_score)` (`orchestrator.py:421`) — collapses the set of per-criterion `scored_by` kinds into one provenance label: `"hybrid"` if any criterion is hybrid; `"deterministic"` if all are; else `"a+b"` joined sorted. Stored alongside the score.
- `_count_fields(summary)` (`orchestrator.py:430`) — the log payload helper for `run_complete`.

---

### `pipeline/worker.py` — the Postgres-backed queue Worker

Module docstring (`worker.py:1`): horizontal scaling = run **N workers against the same database**. Each claims work with **`FOR UPDATE SKIP LOCKED`** so they never collide, and **no external broker (Redis/RabbitMQ) is required**. One job is one crawl batch for one target, so the browser is reused across the batch's URLs.

Job-kind constants: `CRAWL_BATCH = "crawl_batch"` (`worker.py:26`), `ANALYZE_RUN = "analyze_run"` (`worker.py:27`).

#### Enqueue helpers

| Function | Signature | Effect |
|---|---|---|
| `enqueue_batch` (`worker.py:30`) | `(urls, target_name, label=None, max_attempts=4) -> int` | `jobs_repo.enqueue(CRAWL_BATCH, {"urls","target","label"}, max_attempts)`. Returns job id. |
| `enqueue_analysis` (`worker.py:44`) | `(run_id, max_attempts=4) -> int` | `jobs_repo.enqueue(ANALYZE_RUN, {"run_id"}, max_attempts)`. Returns job id. |

Both write a row to the `jobs` table.

#### `Worker.__init__(self, worker_id=None, kinds=None, idle_sleep=5.0)` — `worker.py:50`

- `worker_id` defaults to `f"{socket.gethostname()}:{os.getpid()}"` — unique per process, used as the claim owner.
- `kinds` defaults to `[CRAWL_BATCH, ANALYZE_RUN]` — which job kinds this worker drains.
- `idle_sleep = 5.0` s — how long to sleep when the queue is empty.
- Constructs one `Orchestrator()` to reuse across jobs.

#### `run_forever(self, max_jobs=None) -> int` — `worker.py:61` (the drain loop)

```python
while max_jobs is None or processed < max_jobs:
    job = jobs_repo.claim(self.worker_id, self.kinds)   # FOR UPDATE SKIP LOCKED
    if not job:
        time.sleep(self.idle_sleep); continue
    self._run_job(job); processed += 1
```
`max_jobs=None` runs forever (production); a bounded value exists mainly so tests/CI can run a finite worker. `jobs_repo.claim` is where the `FOR UPDATE SKIP LOCKED` lives — it atomically grabs one unlocked, due job for these kinds.

#### `_run_job(self, job)` — `worker.py:75`

```python
try:
    self._dispatch(job); jobs_repo.succeed(job_id); log.info("job_done", ...)
except Exception as exc:                       # failures are requeued, not fatal
    backoff = _backoff(int(job.get("attempts", 1)))
    jobs_repo.fail(job_id, str(exc), backoff_sec=backoff); log.error("job_failed", ...)
```
Failures are **requeued with backoff**, not fatal, until the job's `max_attempts` is exhausted.

#### `_dispatch(self, job)` — `worker.py:86`

Routes by kind:
- `CRAWL_BATCH`: `target = targets_repo.find(payload["target"])` (raises `ValueError` if unknown), then `asyncio.run(self._orch.run_urls(payload["urls"], target=target, label=...))`. Note: the worker is synchronous and spins up its own event loop per job via `asyncio.run`.
- `ANALYZE_RUN`: `self._orch.analyze_run(int(payload["run_id"]))` (already sync).
- Anything else → `ValueError`.

#### `_backoff(attempts) -> int` — `worker.py:100`

Exponential backoff capped by config:
```python
cfg = get_settings().crawler.retry
return int(min(cfg.max_backoff_sec, cfg.initial_backoff_sec * (2 ** max(0, attempts - 1))))
```
With `config/crawler.yaml → retry` defaults (`initial_backoff_sec: 1.5`, `max_backoff_sec: 30`): attempt 1→1.5s, 2→3s, 3→6s, 4→12s, then capped at 30s.

---

### `pipeline/stages.py` — the three pipeline stages

Module docstring (`stages.py:1`): small, single-responsibility steps the orchestrator wires together; isolating each makes them trivially unit-testable and lets the **worker reuse the exact same logic as the inline CLI path**.

#### `ExtractStage` — `stages.py:26`

Runs every registered extractor over a page's HTML.

- `__init__(self, extractors=DEFAULT_EXTRACTORS)` — the extractor registry (name, fn) tuples from `aeo.extract.DEFAULT_EXTRACTORS`.
- `run(self, page, page_id, pagespeed=None) -> ExtractionBundle` (`stages.py:38`):
  ```python
  bundle = ExtractionBundle(page_id=page_id)
  for name, fn in self._extractors:
      soup = parse(page.html)               # FRESH soup PER extractor
      try:    bundle.put(name, fn(page.html, soup, page.url))
      except Exception as exc:               # one bad extractor must not kill the page
          log.warning("extractor_failed", extractor=name, ...); bundle.put(name, {"error": str(exc)})
  if pagespeed is not None: bundle.put("pagespeed", pagespeed)
  ```

**Key design point — fresh-soup-per-extractor** (`stages.py:27`): `utils.html.body_text` is **destructive** (it `.decompose()`s `script/style/nav/footer`). A shared BeautifulSoup tree would silently break later extractors that need those nodes — `schema_jsonld` and `glossary` read `<script type=ld+json>`, `links` reads `<a>`. So each extractor parses its own tree. Per-extractor `try/except` means a single broken extractor degrades to `{"error": ...}` in the bundle instead of failing the page. PageSpeed is injected separately (it's async/external, fetched outside the extractor loop).

#### `ScoreStage` — `stages.py:53`

Scores a page, honoring the **v4 Parallel Processor toggle**.

- `__init__(self, llm=None)` reads `get_settings().scoring`: `self._parallel = scoring.parallel`, `self._max_workers = scoring.max_workers`.
- `run(self, bundle, run_id) -> PageScore` calls `score_page(bundle, run_id, llm=self._llm, parallel=self._parallel, max_workers=self._max_workers)`.

Config knobs (`config` defaults via `ScoringCfg`): `AEO__SCORING__PARALLEL` (default **False**), `AEO__SCORING__MAX_WORKERS` (default **8**). When parallel, the criterion scorers run concurrently in a thread pool; output is **identical** to sequential because scorers are pure over a shared read-only context. The win is on the I/O-bound LLM-refined criteria.

#### `PersistStage` — `stages.py:71`

A single, mockable persistence seam over the repos (so tests can swap it out):

| Method | Calls | DB table |
|---|---|---|
| `page(page, run_id, client_id, competitor_id) -> StoredPage` | `pages_repo.upsert(...)` | `crawled_pages` |
| `extraction(bundle) -> int` | `extractions_repo.put(bundle)` | extractions |
| `score(page_score, scored_by) -> int` | `scores_repo.put(page_score, scored_by)` | `rubric_scores_v2` |
| `copy_unchanged(url_normalized, page_id, run_id) -> bool` | `extractions_repo.copy_latest_for_url` AND `scores_repo.copy_latest_for_url` | extractions + scores |

`copy_unchanged` (`stages.py:83`) is the fingerprint clone-forward: **both** the extraction copy and the score copy must succeed for it to count as a clean copy (returns the `and` of the two booleans). If either fails, the orchestrator falls through to a normal extract+score.

---

### `pipeline/analysis.py` — per-page analysis wiring (back half)

Module docstring (`analysis.py:1`): after a page is crawled/extracted/scored, this runs the remaining steps as one isolated unit — **Gap analysis → Validate (recommend + simulate + retry ≤3) → Report**. Each step is wrapped in `trace_step` for observability; the orchestrator wraps the whole call in the Error Sink.

Module-level mapping: `_TIER_COLUMNS = {name: f"{name}_score" for name in SCORERS}` (`analysis.py:52`) — maps each rubric criterion to its `rubric_scores_v2` tier column.

#### `AnalysisResult` — `analysis.py:55`

`@dataclass(slots=True)` carrying `page_id, run_id, intent, gap, validation, report, independent`.

#### `build_competitor_pool(rows, reference) -> list[CompetitorPage]` — `analysis.py:66`

Turns competitor score rows (`rubric_scores_v2` + `url`) into gap-analysis candidates **without reloading any competitor extraction bundle**:
- For each row, build a `tiers` dict from the `*_score` columns that are non-null; skip rows with no tiers.
- `intent = reference.classify_intent(row["url"])` — the documented **lightweight URL-only heuristic** (no bundle reload).
- Emit `CompetitorPage(page_id, intent, total, tiers)`.

#### `analyze_page(*, bundle, score, url, reference=None, rubric=None, llm=None, competitors=None, page_type=None, intent=None, persist=True, trace=True, perplexity=None, independent=False, question=None) -> AnalysisResult` — `analysis.py:90`

The orchestration for one scored page. Steps (each in a `_step` trace context):

1. **Defaults / intent:** loads reference + rubric if not passed. If `intent is None`, `intent = reference.classify_intent(url, _headings(bundle))` (URL + H2/H3 headings). `competitor = select_competitor(competitors or [], intent)` — picks the best competitor page for that intent.
2. **Gap** (`_step("processor", ..., "gap")`): `gap = analyze_gap(score, reference=..., rubric=..., competitor=..., intent=...)`; if `persist`, `persist_gap(gap)`. This is the Dual-Layer Gap Analysis.
3. **Validate** (`_step("validator", ..., "validate", model=...)`): `validation = validate_page(bundle, gap, url=..., reference=..., rubric=..., llm=..., page_type=..., persist=...)`. This is the v3 **edit-efficacy gate** — does the proposed fix raise the deterministic score? (`model` is the LLM model name when the LLM is enabled, else `None`.)
4. **Independent validation (the v4 addition, `independent=True`)** (`_step("validator", ..., "independent")`):
   ```python
   independent_verdict = validate_independent(bundle, url=url, question=question, perplexity=perplexity)
   if persist and independent_verdict.citation is not None and independent_verdict.citation.available:
       _record_citation(page_id, run_id, url, independent_verdict)
   ```
   Design intent (docstring, `analysis.py:107`): v3 validation is *circular* (it re-scores using the same rubric the recommender optimized for). The v4 Independent Validator additionally checks **non-circular** signals — liftable TL;DR, H1-as-question, valid JSON-LD — and, if a Perplexity client is enabled, the **real-world citation test** (does the page actually get cited for the target question?). This fixes v3's circular validation; the citation outcome is logged for the "validated-wins" loop.
5. **Report** (`_step("reporter", ..., "report")`): `report = build_report(url=..., score=..., gap=..., validation=..., page_type=..., intent=..., independent=independent_verdict)`; if `persist`, `persist_report(report)`.
6. Returns the assembled `AnalysisResult`.

**Side effects (when `persist=True`):** writes gap, validation, citation-feedback, and report rows. **Network:** the validate step may call the LLM; the independent step may call Perplexity.

#### `_record_citation(page_id, run_id, url, verdict)` — `analysis.py:160`

Logs the Perplexity citation outcome for the **validated-wins feedback loop**: imports `feedback as feedback_repo` locally and calls `feedback_repo.record_citation(page_id, run_id, url, question=..., cited=cit.cited, evidence={"matched": cit.matched, "citations": cit.citations})`. No-op if `verdict.citation is None`. **DB table touched:** feedback/citation table.

#### Status predicates

- `is_improved(result)` (`analysis.py:175`) → `result.validation.status == STATUS_IMPROVED`.
- `is_could_not_improve(result)` (`analysis.py:179`) → `... == STATUS_COULD_NOT_IMPROVE` (the page that exhausted the retry cap and routes to Human Review).

#### Helpers

- `_step(trace, agent, run_id, page_id, step, *, model=None)` (`analysis.py:188`) — returns `trace_step(...)` when `trace` is on, else a DB-free `nullcontext()`. This is what lets unit tests run `analyze_page(trace=False)` with no observability DB.
- `_headings(bundle)` (`analysis.py:195`) — pulls H2+H3 from the headings extractor for intent classification.

---

### `pipeline/reference_arch.py` — Reference-Architecture DB glue

Module docstring (`reference_arch.py:1`): the DB-touching glue for the v4 generator and Coverage Diff, **kept out of the Orchestrator so that class stays readable**. Two best-effort steps run at the front of a site run, isolated so a failure (no competitor data, generator hiccup, transient DB) logs and is skipped. The pure seam `discovered_pages` is unit-tested directly.

- `_domain(url)` (`reference_arch.py:43`) — host of a URL, lowercased, `www.` stripped.

- `build_competitor_patterns(allowed_entities) -> CompetitorPatterns` (`reference_arch.py:48`) — **L1, the empirical floor.** Reads `scores_repo.latest_competitor_scores()`, loads each competitor's bundle via `extractions_repo.get(...)`, collects domains, and calls `extract_patterns(pages, allowed_entities=..., domains=...)`. Returns an empty-but-valid pattern set when no competitors have been crawled yet.

- `generate_and_pin_blueprint(run_id, *, topic=None, llm=None) -> StoredBlueprint | None` (`reference_arch.py:62`):
  1. `cfg = get_settings().reference_architecture`; `if not cfg.enabled: return None` (env `AEO__REFERENCE_ARCHITECTURE__ENABLED`, default **True**).
  2. `framework = load_framework()` (loads `config/framework.yaml`); `topic = topic or cfg.topic or framework.topic` (default `"PEV"`).
  3. `patterns = build_competitor_patterns(framework.required_entities)` (L1).
  4. `blueprint = generate_blueprint(topic=..., framework=framework, patterns=patterns, llm=llm)` — L1 + L2 (framework) + L3 (LLM synthesis).
  5. `stored = blueprints_repo.save_versioned(blueprint)` — **reuse-or-bump** versioning; `blueprints_repo.pin_run(run_id, stored.id)` pins it so the "measuring stick" doesn't move week-to-week.
  6. Logs `blueprint_pinned` (version, `reused`, generator, node count). **DB tables:** `blueprints` (+ run pin).

- `discovered_pages(scored, reference) -> list[DiscoveredPage]` (`reference_arch.py:83`) — **pure** (the unit-tested seam): maps prioritized `ScoredUrl`s to the Coverage Diff's view, using `normalize_slug(s.url)` and `reference.classify_intent(s.url)`; page-type comes straight from the prioritizer.

- `compute_and_persist_coverage(run_id, stored, scored, *, target_id, reference=None) -> CoverageDiffResult` (`reference_arch.py:97`) — `discovered = discovered_pages(scored, reference)`; `result = coverage_diff(stored.blueprint, discovered)`; persists via `coverage_repo.put(run_id, blueprint_id=stored.id, target_id=..., coverage_pct=..., missing_count=len(result.missing), thin_count=len(result.thin_clusters), detail=result.to_detail())`; logs `coverage_diff_persisted`. **DB table:** `coverage`.

---

### Configuration files reference (`config/*.yaml`)

All values below are overridable via env vars with the `AEO__` prefix and `__` nesting delimiter (e.g. `AEO__CRAWLER__CONCURRENCY=8`), per `Settings.model_config` in `settings.py`. The scoring/extractor/entities/prioritization/best-practices/framework YAMLs are loaded by their respective loaders, not all via pydantic-settings.

#### `config/scoring.yaml` — the 10-criterion rubric (single source of truth for scoring)

Top-level keys:
- `scale: {min: 1, max: 5}` — every criterion scores 1–5.
- `criteria:` — one block per criterion. Each has a `label` and `weight` (default `1.0` ⇒ max total 50). Criteria 1–8 are the shipped hard contract; 9–10 (`render_accessibility`, `answer_readability`) were added in v3.

Per-criterion knobs (actual shipped values):

| Criterion | Key thresholds / vocab |
|---|---|
| `schema_markup` | `valued_types`: FAQPage, HowTo, TechArticle, Article, NewsArticle, Organization, DefinedTerm, ItemList, BreadcrumbList, Product, BlogPosting |
| `qa_blocks` | `min_answer_chars: 80`; `question_words`: what, why, how, when, where, who, which, is, are, do, does, can, should |
| `stats_in_html` | tier→count `tiers: {1:0, 2:1, 3:3, 4:6, 5:10}` (distinct concrete numeric claims) |
| `entity_consistency` | `tiers` keyed by entity:first-person mention **ratio** `{1:0.0, 2:0.5, 3:1.0, 4:1.5, 5:2.5}` (>1 = entity-dominant = good) |
| `heading_structure` | `tiers` = % of H2/H3 that are questions/named-concepts `{1:0, 2:0.10, 3:0.25, 4:0.45, 5:0.65}`; `penalty_missing_h1: 1`; `penalty_template_h1: 1` |
| `content_depth` | `min_word_count_for_credit: 400`; `methodology_keywords`: methodology, dataset, sample size, n=, study, research, analysis, findings, results, evidence, framework, protocol |
| `citation_signals` (E-E-A-T) | `authority_domains` (nist.gov, cisa.gov, nvd.nist.gov, mitre.org, owasp.org, ietf.org, first.org, sans.org, verizon.com, microsoft.com, google.com, cloudflare.com); `credentials` (CISSP, OSCP, OSCE, CEH, GIAC, GPEN, GWAPT, GCIH, GCFA, CISA, CISM, CRISC, CCSP, OSWE, OSED); `tiers` requiring author/date/N authority links (tier 5 = author+date+5 links) |
| `load_speed` | mobile PSI thresholds `tiers: {1:0, 2:30, 3:50, 4:75, 5:90}`; `penalty_js_only_content: 1` |
| `render_accessibility` (crit 9) | inflation ratio = rendered/initial text, **lower is better**: `inflation_max: {5:1.5, 4:2.5, 3:4.0, 2:8.0}` (above tier-2 → tier 1; js-only → tier 1); `min_initial_text_chars: 200` |
| `answer_readability` (crit 10) | Flesch bands `flesch_tiers: {1:0, 2:20, 3:30, 4:45, 5:55}`; `max_avg_sentence_len: 28` (docks a point above); `min_chunks_for_credit: 3`; `min_word_count: 50` (below = floor) |

#### `config/extractors.yaml` — deterministic-extractor regex packs / pickers

- `stats.patterns` — regexes for "concrete" numbers: `percent`, `multiplier` (`5x`), `money`, `big_num`, `cve` (`CVE-####-####+`), `cwe`, `cvss`, `time_unit`. Strategy: over-collect, filter downstream.
- `stats.blacklist_patterns` — discard hits that are page numbers, figure refs, or copyright-year `©`.
- `authority_links.domains_file: ../config/scoring.yaml` — **reuses** `citation_signals.authority_domains` rather than duplicating it.
- `author_selectors` — `meta` / `jsonld_paths` (`author.name`, `creator.name`) / `css` (`.author`, `.byline`, `[rel=author]`, `[itemprop=author]`), tried in order, first hit wins.
- `date_selectors` — analogous `meta` + `css` (`time[datetime]`, `.published`, `[itemprop=datePublished]`, …).
- `render_mode` — `js_inflation_ratio: 3.0`, `min_initial_text_chars: 200` (the JS-dependent-page penalty thresholds used at extract time).
- `template_h1_patterns` — CMS template bugs to flag: `^Resources$`, `^Welcome$`, `^Home$`, `^Page Not Found$`.

#### `config/entities.yaml` — entity dictionary (extract/entities + scoring/entity_consistency)

`entities:` maps each company to `canonical`, `aliases`, `first_person` markers (`we/our/us/…`), and `domain`. Shipped set: **Securin** (the client, securin.io) plus competitors **Pentera, Cymulate, XM Cyber, AttackIQ, Picus Security, Hive Pro, Ridge Security, SecureLayer7**. Matching is case-insensitive; variants matched as whole words. This vocabulary drives the entity:first-person ratio in `entity_consistency` scoring.

#### `config/crawler.yaml` — crawler runtime knobs (env `AEO__CRAWLER__*`)

- `user_agent: "AEOBot/0.2 (+https://securin.io/bot)"`.
- `concurrency: 4`; `request_timeout_sec: 30`.
- `respect_robots: true` (honors robots.txt + crawl-delay).
- `rate_limit:` token-bucket per host — `requests_per_minute: 30`, `burst: 5`.
- `retry:` `max_attempts: 4`, `initial_backoff_sec: 1.5`, `max_backoff_sec: 30` (these feed `worker._backoff`), `retry_on_status: [408, 425, 429, 500, 502, 503, 504]` (everything else is permanent).
- `fingerprint:` `enabled: true`, `algorithm: sha256` — gates the unchanged-page short-circuit in `_process_one`.
- `browser:` Crawl4AI tuning — `headless: true`, `remove_overlay_elements: true`, `word_count_threshold: 0`.
- `discovery:` Site Discovery (used by `run_site`/`audit_cycle`) — `max_urls: 200`, `max_depth: 2`, `max_sitemaps: 50`, `timeout_sec: 15`. Sitemap-first, recursive-BFS fallback, plain HTTP GETs.

#### `config/prioritization.yaml` — Page Prioritization (loaded by `load_prioritization_cfg`)

Ranking formula (file header): `final_score = base_weight(page_type) × traffic_signal`, ranked desc, top-N selected. Traffic signal = internal-link count today (a GSC export can replace it later).
- `top_n: 30` — how many top-ranked URLs feed the per-page pipeline (the `selected` set in `run_site`).
- `min_traffic_signal: 1.0` — floor so a page with zero inbound links still ranks by base weight (avoids ×0).
- `default_type: default`.
- `base_weights:` per page-type — `pillar 1.0`, `product 0.9`, `solution 0.9`, `blog 0.8`, `homepage 0.7`, `about 0.4`, `contact 0.3`, `utility 0.2`, `default 0.5`.
- `homepage_paths: [/home, /index.html, /index.htm]` (plus bare `/`).
- `precedence: [utility, blog, pillar, solution, product, contact, about]` — first-match-wins classification order (so `/blog/new-product` → blog, not product).
- `url_patterns:` substring needles per page-type (singular needles also catch plurals), e.g. `utility: [/login, /privacy, …]`, `pillar: [/resource, /guide, /glossary, /what-is, …]`, `contact: [/contact, /demo, /pricing, /free-trial, …]`.

#### `config/best_practices.yaml` — Reference Layer (the 60% best-practice layer)

The baseline the Dual-Layer Gap Analysis scores against. PROVISIONAL.
- `targets:` per-criterion target score (1–5); `gap = max(0, target − actual)`. Most are `4`; `render_accessibility: 5` (JS-only content is invisible to answer engines).
- `architecture:` ideal content structure per page-type — `must_have`, `headings`, `target_word_count` (e.g. `pillar` → TechArticle schema, 2000 words; `blog` → Article + author/date, 1200 words; `default` fallback → 700 words).
- `query_intent:` the lightweight intent heuristic — `default: informational`; `url_patterns` and `heading_keywords` for `commercial` / `navigational` / `informational`; precedence commercial > navigational > informational.

#### `config/framework.yaml` — Reference Architecture Framework (L2 guardrail + ceiling)

The curated framework the v4 generator combines with competitor patterns (L1) and the LLM (L3) to synthesize a versioned blueprint. Seeded for ONE topic (PEV) per the v4 build sequence; generalizing = add another topic block, no code change.
- `version: "1"` — bump whenever structure changes so blueprints re-version (matches `AEO__REFERENCE_ARCHITECTURE__FRAMEWORK_VERSION`).
- `topic: PEV`.
- `required_entities:` the topic's vocabulary the generator may reference (generator guardrail — it can't hallucinate categories): MITRE ATT&CK, CVSS, EPSS, KEV, CTEM, BAS, RemOps, CISA, NIST, Attack Surface.
- `journey_stages: [awareness, consideration, decision]`.
- `clusters:` topical-authority clusters, each with a `pillar` + `supporting` pages and `min_pages` (the **thin-cluster threshold** the Coverage Diff flags against — `10` for ctem/continuous-validation/exposure-management, `8` for remediation; default `min_pages_per_cluster: 10` in settings). Each node carries `slug, title, page_type, intent, journey_stage, required_entities, seed_questions`.
- `standalone_nodes:` non-cluster pages the ideal site must have (`/`, `/platform`, `/contact`).
- `criteria_definitions:` the **ceiling** half of L2 — `perfect` vs `average` page descriptions, `checkable` items, and `schema_org` mapping per criterion; `target` mirrors `best_practices.yaml`. Read by the generator and the validated-wins loop.

---

### How it all flows (one-paragraph mental model)

`audit_cycle` (weekly) → `run_site` does Discovery (`crawler.yaml → discovery`) → Prioritization (`prioritization.yaml`) → isolated blueprint+coverage block (`framework.yaml`, `reference_arch.py`) → `_run_pages` crawls via `fetch_many`, batches PageSpeed (≤5 concurrent), and for each page `_process_one` either short-circuits on an unchanged content-hash (`crawler.yaml → fingerprint`, cloning prior extraction+score forward) or runs `ExtractStage` (fresh soup per extractor) + `ScoreStage` (`scoring.yaml`, optional parallel) + persist. Then `analyze_run` fans pages across a thread pool (`AEO__VALIDATION__ANALYSIS_CONCURRENCY`), and `_analyze_one` → `analyze_page` runs Gap (`best_practices.yaml`) → Validate → optional Independent-Validate (deterministic checks + Perplexity citation, recorded for the validated-wins loop) → per-page Report, each Error-Sink isolated. Finally `_build_and_persist_site_report` rolls the coverage diff + pinned blueprint + per-page reports into one site report. The `Worker` simply drains `crawl_batch` / `analyze_run` jobs from Postgres (`FOR UPDATE SKIP LOCKED`) and calls the same Orchestrator — no broker, no graph framework.

---

## Appendix A — Data-flow map (which step writes which table)

Every phase reads and writes a well-defined set of PostgreSQL tables. This is the
quickest way to reason about "where did this number come from?".

| Step | Reads | Writes |
|---|---|---|
| Discovery / Prioritization | — (network) | `crawl_runs`, `page_priorities` |
| Blueprint (generate + pin) | `rubric_scores_v2`, `page_extractions` (competitors), `config/framework.yaml` | `blueprints`, `crawl_runs.blueprint_id` |
| Coverage Diff | `blueprints`, `page_priorities` (the discovered set) | `coverage_diffs` |
| Crawl + Persist | — (network) | `crawled_pages` |
| Extract | `crawled_pages` (in-memory bundle) | `page_extractions` |
| Score | `page_extractions`, `config/scoring.yaml` | `rubric_scores_v2` |
| Gap analysis | `rubric_scores_v2`, competitor pool, `config/best_practices.yaml` | `page_gaps` |
| Validate (recommend→simulate→retry) | `page_gaps`, `page_extractions` | `recommendations` |
| Independent Validate | `page_extractions` (+ Perplexity) | `citation_results` |
| Report (per page) | all of the above | `page_reports` |
| Site report | `coverage_diffs`, `blueprints`, `page_reports` | `site_reports` |
| Feedback (propose) | `citation_results` ⋈ `rubric_scores_v2` (same-run) | `criteria_refinements` (`status='proposed'`) |
| Queue worker | `aeo_jobs` (`FOR UPDATE SKIP LOCKED`) | `aeo_jobs`, then the crawl/score tables |
| Observability | all steps emit | `agent_traces` |

**Key correctness invariant (V4 fix):** the feedback loop joins a citation to the
rubric tiers from the **same run** (`feedback.recent_observations`,
`storage/repos/feedback.py`) via `LEFT JOIN … ON s.page_id = cr.page_id AND s.run_id
= cr.run_id`. A page re-scored in a later run no longer contaminates an earlier
run's citation outcome, and an unscored cited page surfaces with empty tiers
instead of vanishing.

---

## Appendix B — Glossary

- **AEO (Answer Engine Optimization).** Optimizing content so AI answer engines
  surface and *cite* it. The whole product measures and improves AEO.
- **Rubric.** The ten scored criteria (`config/scoring.yaml`). Each criterion
  scores **1–5**; the page total is out of **50**.
- **Tier.** A criterion's 1–5 score for one page. Computed deterministically from
  HTML signals via thresholds in the rubric.
- **Extractor.** A pure function `extract(html, soup, url) -> dict` that pulls one
  family of signals from a page. Extractors never score; they only observe.
- **Scorer.** A pure function that reads a `ScoreContext` and maps extracted
  signals to a 1–5 tier for one criterion.
- **ScoreContext.** The bundle of (extraction data + rubric + LLM handle) a scorer
  reads; the single argument every scorer takes.
- **Blueprint (v4).** The versioned, per-topic *ideal site*: an ideal sitemap
  (`SitemapNode`s) + a coverage map (clusters, required entities, journey stages).
  Built by the **Reference Architecture Generator**.
- **L1 / L2 / L3.** The three blueprint inputs: **L1** = empirical competitor
  structural patterns; **L2** = the curated framework guardrail
  (`config/framework.yaml`); **L3** = optional Gemini synthesis that enriches the
  L1+L2 floor within the L2 vocabulary.
- **content_hash / reuse-vs-bump.** The blueprint hashes its *inputs*; identical
  inputs reuse the pinned version (so week-over-week scores stay comparable), any
  structural change bumps the version.
- **Coverage Diff (v4).** Site-level gap: the discovered sitemap vs the ideal
  sitemap → `coverage_pct`, **missing** nodes, **thin** clusters.
- **Dual-Layer Gap Analysis.** The per-page target = **60% best-practice + 40%
  best competitor**; gaps are the shortfall against that target.
- **Independent Validator (v4).** Re-checks a recommendation against **non-circular**
  signals (liftable TL;DR, H1-as-question, valid JSON-LD) plus a **Perplexity
  citation test** — fixing v3's circular "re-score with the same rubric" validation.
- **Validated-wins feedback loop.** Pages that provably get cited propose nudges to
  a criterion's *target*; **human-gated**, never auto-applied.
- **Error Sink.** The `page_guard` context that isolates a single page's failure so
  a run continues (`obs/error_sink.py`).
- **Fingerprint short-circuit.** Skip extraction + scoring for a page whose content
  hash is unchanged from a prior run, cloning the prior results forward.
- **Force-IPv4 transport.** An httpx transport seam (`crawl/transport.py`) that
  binds `local_address="0.0.0.0"` so HTTP clients use IPv4 — needed on OCI Ampere
  where IPv6 egress is unreliable.

---

## Appendix C — How to run it

### Operating modes

| Goal | Command |
|---|---|
| Apply DB schema | `aeo migrate` |
| Audit a whole domain (discover→prioritize→crawl→extract→score) | `aeo audit DOMAIN -t NAME` |
| Score an explicit URL list (full pipeline) | `aeo run URLS… -t NAME` |
| Crawl/extract only (score later) | `aeo crawl URLS… -t NAME` then `aeo score -r RUN` |
| Per-page analysis (gap→recommend→validate→report) | `aeo analyze -r RUN` |
| Queue + workers | `aeo enqueue URLS… -t NAME` then `aeo worker` |
| **Weekly audit loop (v4, end-to-end)** | `aeo audit-cycle DOMAIN -t NAME` |
| Blueprint generate / inspect | `aeo blueprint generate` · `aeo blueprint show` |
| Site-level coverage / report | `aeo coverage -r RUN` · `aeo site-report -r RUN` |
| Criteria-refinement proposals (human-gated) | `aeo refinements [--propose]` |
| Health / queue depth / run report | `aeo status [-r RUN]` |
| A page's agent journey (observability) | `aeo trace PAGE_ID` |

### Configuration

- **Settings** are layered: built-in defaults → `config/*.yaml` → environment
  variables using the **`AEO__SECTION__KEY`** double-underscore nesting (e.g.
  `AEO__SCORING__PARALLEL=true`, `AEO__VALIDATION__ANALYSIS_CONCURRENCY=4`).
  `DATABASE_URL` and `PSI_API_KEY` are read directly.
- **Secrets** live only in the environment. The PSI key is sent via the
  `x-goog-api-key` header (never in a URL/log). No Gemini/Perplexity key present →
  those paths run in deterministic-fallback mode until keyed.

### Scheduling the weekly loop

`ops/` ships a systemd service+timer (`aeo-audit.service` / `aeo-audit.timer`), a
`crontab.example`, and `weekly_audit.sh`. The loop is self-scheduled by the OS —
the orchestrator does not run its own scheduler (see Design decisions).

### Tests

`pytest -q` runs the full offline suite (no DB, browser, or LLM). DB round-trips
live in `tests/integration/test_db_smoke.py` and **skip** unless a Postgres is
reachable. `ruff check src tests` and `mypy` gate style and types.

---

## Appendix D — Design decisions (and why)

1. **Kept the async `Orchestrator`; did *not* adopt LangGraph.** The v4 diagram
   labels the conductor "LangGraph", but the existing async orchestrator already
   sequences the stages, and `audit_cycle` + an OS timer (`ops/`) satisfy the
   "LangGraph doesn't schedule itself" need. Adopting LangGraph would add a heavy
   dependency for no functional gain.
2. **Retained Crawl4AI/Playwright; did *not* switch to FireCrawl.** The diagram
   says FireCrawl; the working, tested render path is Crawl4AI. A FireCrawl backend
   could be added behind the existing crawl-client seam if ever desired.
3. **Deterministic-first everywhere.** Gemini reuses the existing cloud LLM
   backend; Perplexity is an injectable seam; both have deterministic fallbacks.
   The product is fully functional with zero AI keys configured.
4. **Postgres-as-queue.** Avoids operating a separate broker; `FOR UPDATE SKIP
   LOCKED` gives safe multi-worker draining.
5. **Topic layer is best-effort and isolated.** Blueprint/coverage failures log
   `reference_architecture_skipped` and never abort the crawl.
6. **Human-gated learning.** The feedback loop only *proposes*; a human edits
   `config/best_practices.yaml`. This is the deliberate guard against the system
   training on its own outputs.

---

## Appendix E — What V4 added on top of V3 (the delta)

| # | V4 capability | Where |
|---|---|---|
| 1 | Versioned **Blueprint** contract (reuse-vs-bump on input hash) | `reference/blueprint.py`, `storage/repos/blueprints.py` |
| 2 | **Reference Architecture Generator** (L1 patterns / L2 framework / L3 LLM) | `reference/{competitor_patterns,framework,generator}.py`, `config/framework.yaml` |
| 3 | Site-level **Coverage Diff** | `processor/coverage_diff.py`, `coverage_diffs` |
| 4 | **Independent Validator** (non-circular signals + Perplexity) | `validation/independent.py`, `nlp/perplexity.py` |
| 5 | **Parallel processor** (scorers + analysis fan-out, byte-identical output) | `scoring/scorers/__init__.py`, `pipeline/orchestrator.py:248` |
| 6 | **Validated-wins** feedback loop (human-gated) | `reference/feedback.py`, `criteria_refinements` |
| 7 | **Weekly audit loop** + force-IPv4 + `ops/` units | `pipeline/orchestrator.py:295`, `crawl/transport.py`, `ops/` |
| 8 | Site-level **report** | `report/site_builder.py`, `site_reports` |
| — | Migration **0009** for all of the above | `storage/migrations/0009_v4_reference_architecture.sql` |

All seven gaps are implemented with the page layer fully preserved; the adversarial
review's 10 findings were fixed and regression-tested (see
`docs/MIGRATION_V3_V4.md` for the review table).

---

## Appendix F — Producing a PDF

This document is Markdown. To hand a team leader a PDF:

```bash
# with pandoc + a LaTeX engine (best quality)
pandoc docs/PIPELINE_EXPLAINED.md -o PIPELINE_EXPLAINED.pdf \
       --toc --toc-depth=3 -V geometry:margin=1in

# or, no LaTeX: render to HTML and "Print to PDF" from a browser
pandoc docs/PIPELINE_EXPLAINED.md -s --toc -o PIPELINE_EXPLAINED.html
```

If `pandoc` isn't installed, any Markdown viewer (VS Code preview, GitHub) renders
it faithfully, and most can export to PDF.
