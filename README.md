<div align="center">

# 🛰️ AEO Crawler

### Answer Engine Optimization auditing for cybersecurity content

Crawl, extract, and score web pages against a **10-criterion AEO rubric** to measure how well
content surfaces in AI answer engines — then generate the recommendations to close the gaps.

[![CI](https://github.com/Sanjith72/aeo-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/Sanjith72/aeo-pipeline/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-365%20offline-brightgreen.svg)](#testing)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Status: Beta](https://img.shields.io/badge/status-v4.1%20beta-orange.svg)](BETA_RELEASE_NOTES.md)

</div>

---

## What it does

**AEO Crawler** audits Securin and competitor pages on the ten criteria that determine how well
content gets cited by AI answer engines (ChatGPT, Perplexity, Gemini, …). It crawls a site,
extracts structural signals from the HTML, scores each page on a reproducible **0–50 rubric**, and
produces per-page and site-level reports with concrete, validated recommendations.

Scoring is **deterministic-first**: all ten criteria score from parsed HTML signals alone. A local
LLM (Ollama) is optional and only *refines* content-depth analysis — disable it and you still get a
complete, reproducible score. The same inputs always produce the same number.

### Highlights

- 🎯 **10-criterion rubric (max 50)** — schema markup, Q&A blocks, stats, entity consistency, headings, depth, E-E-A-T, load speed, render accessibility, readability.
- 🔁 **Deterministic & reproducible** — thresholds, weights, and vocabularies live in `config/`, not in code. The LLM is optional.
- 🏗️ **Two-layer audit** — page-level scoring *plus* a topic-level **Reference Architecture** that builds an ideal-site blueprint and measures coverage gaps against it.
- ✅ **Non-circular validation** — recommendations are re-checked against independent signals (liftable TL;DR, H1-as-question, valid JSON-LD) plus a live citation test.
- 🐘 **PostgreSQL-only** — the database is both the job queue (`FOR UPDATE SKIP LOCKED`) and the result store. No external broker.
- 📄 **Deliverable reports** — per-page and site-level AEO/SEO reports, with optional PDF export.
- 🔭 **Observable** — every agent step is traced (`agent_traces` + `aeo trace`); optional OpenTelemetry OTLP export.
- 🧪 **365 offline tests** — the full suite runs with no DB, browser, or LLM required.

---

## Table of contents

- [The rubric](#the-rubric--10-criteria)
- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Documentation](#documentation)
- [Requirements](#requirements)
- [License](#license)

---

## The rubric — 10 criteria

Each criterion scores **1–5** (max **50**). Criteria 1–8 are the original shipped contract; **9–10**
were added in v3 when the rubric expanded 8 → 10.

| #  | Criterion | What it measures |
|----|-----------|------------------|
| 1  | **Schema Markup** | High-value JSON-LD types (`FAQPage`, `TechArticle`, …); flags the glossary `DefinedTerm` gap. |
| 2  | **Q&A Blocks** | Real question → answer pairs; bonus for `FAQPage` schema. |
| 3  | **Stats in HTML** | Distinct concrete numeric claims (percentages, money, CVE/CVSS…). |
| 4  | **Entity Consistency** | Brand/entity mentions vs. first-person ("we/our") language. |
| 5  | **Heading Structure** | Question-phrased H2/H3s; penalties for missing/template H1. |
| 6  | **Content Depth** | Length, methodology language, stats, promotional tone *(LLM-refined)*. |
| 7  | **Citation Signals (E-E-A-T)** | Author, date, and authoritative external links. |
| 8  | **Load Speed** | PageSpeed Insights mobile score; JS-only-content penalty. |
| 9  | **Render Accessibility** | Is the answer in server-rendered HTML, not JS-injected? Penalizes render-inflation. |
| 10 | **Answer Readability** | Flesch reading ease, sentence length, passage segmentation — can an engine lift a clean answer? |

> The rubric lives in [`config/scoring.yaml`](config/scoring.yaml) — thresholds, weights, and
> vocabularies are **config, not code**.

---

## How it works

```
URLs ─► Crawl (Crawl4AI / Playwright) ─► Extract (12 pure extractors)
                                               │
                                               ▼
                         Score (10 deterministic scorers, LLM optional)
                                               │
                                               ▼
                     PostgreSQL: pages · extractions · rubric_scores_v2
```

`aeo audit <domain>` prepends **Site Discovery** (sitemap + recursive) and **Page Prioritization**
(rank by value, cut to top-N) so the per-page loop spends its budget only on the highest-value URLs.
The back half — `aeo analyze` — runs **Dual-Layer Gap Analysis → Recommender → Validation (≤3
retries) → per-page report**, with an Error Sink isolating each page and an Observability layer
recording every step.

- **Extractors** are pure `extract(html, soup, url) -> dict` functions. Each gets a fresh `BeautifulSoup` (text extraction is destructive by design).
- **Scorers** read a single `ScoreContext` and map raw signals to a 1–5 tier using rubric thresholds. One scorer failing never aborts a page — it floors and records the error.
- **PostgreSQL** is both the job queue and the result store. No external broker.

**v4 adds a topic layer on top of the page layer.** Once per run, the **Reference Architecture
Generator** builds a versioned *blueprint* — the ideal sitemap + coverage map for a topic — by
combining competitor structural patterns (L1), a curated framework (L2, `config/framework.yaml`),
and optional LLM synthesis (L3). The **Coverage Diff** measures the site against that blueprint
("which pages are missing"), and the **Independent Validator** re-checks recommendations against
non-circular signals plus a citation test. `aeo audit-cycle` chains the whole thing for the weekly
loop.

📖 Full design rationale and module map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/MIGRATION_V3_V4.md](docs/MIGRATION_V3_V4.md).

---

## Quickstart

```bash
python -m venv .venv && .venv\Scripts\activate     # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium

cp .env.example .env            # set DATABASE_URL
aeo migrate                     # create the schema
aeo run https://securin.io/blog/some-post -t Securin
```

Prefer containers?

```bash
docker compose up -d db
docker compose run --rm migrate
docker compose up -d worker
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for cloud deploy, CI/CD, scaling, scheduling, secrets, and backups.

---

## CLI reference

```text
# Core pipeline
aeo migrate                  apply pending DB migrations
aeo audit  DOMAIN -t NAME    discover → prioritize → crawl → extract → score
aeo discover DOMAIN          discover + rank a site's URLs (no crawl, no DB)
aeo run    URLS… -t NAME     crawl → extract → score (full pipeline)
aeo crawl  URLS… -t NAME     crawl → extract only (score later)
aeo score  -r RUN_ID         score a run's extracted-but-unscored pages
aeo analyze -r RUN_ID        gap → recommend → validate (≤3) → per-page report
aeo enqueue URLS… -t NAME    queue a crawl batch for a worker
aeo worker                   drain the job queue
aeo status [-r RUN_ID]       DB health, queue depth, run report
aeo trace  PAGE_ID           dump a page's agent journey (observability)
aeo report TARGET            render the per-page AEO/SEO reports (deliverable)

# v4 — Reference Architecture
aeo audit-cycle DOMAIN -t N  weekly loop: blueprint → coverage → crawl → analyze → site report
aeo blueprint generate       generate (or reuse) the versioned ideal-site blueprint
aeo blueprint show           print a stored blueprint (ideal sitemap + coverage map)
aeo coverage    -r RUN_ID    site-level Coverage Diff (missing / thin pages)
aeo site-report -r RUN_ID    render the site-level AEO report
aeo refinements [--propose]  validated-wins criteria-target proposals (human-gated)

# v4.1 (beta) — onboarding · dry-run · OpenTelemetry
aeo audit DOMAIN --dry-run   in-memory preview; writes NOTHING to the DB
```

**v4.1 notes** — drop a `config/domains/<domain>.yaml` to set topic / engine target / max URLs /
label per client; set `AEO__OBS__OTEL_ENABLED=true` + `pip install -e ".[otel]"` to export OTLP
spans; the scoring-contract configs are folded into the blueprint version hash for week-over-week
comparability. See [BETA_RELEASE_NOTES.md](BETA_RELEASE_NOTES.md).

---

## Configuration

Settings are layered: **defaults → `config/*.yaml` → environment** (`AEO__SECTION__KEY`).

| File | Purpose |
|------|---------|
| [`config/scoring.yaml`](config/scoring.yaml) | The rubric: thresholds, weights, vocabularies. |
| [`config/framework.yaml`](config/framework.yaml) | Curated L2 framework for the Reference Architecture. |
| [`config/crawler.yaml`](config/crawler.yaml) | Crawl politeness, retries, fingerprinting. |
| [`config/prioritization.yaml`](config/prioritization.yaml) | Page-value ranking for top-N selection. |
| [`config/extractors.yaml`](config/extractors.yaml) · [`config/entities.yaml`](config/entities.yaml) · [`config/best_practices.yaml`](config/best_practices.yaml) | Extractor tuning, entity lists, best-practice targets. |
| `config/domains/<domain>.yaml` | Per-client onboarding: topic, engine target, max URLs, label. |

Optional dependency groups: `.[otel]` (OTLP export), `.[pdf]` (PDF reports), `.[embeddings]`, `.[dev]`.

---

## Project structure

```text
src/aeo/
  cli.py                 typer CLI (entry point: `aeo`)
  settings.py            layered config (defaults → YAML → env)
  crawl/                 site discovery, Crawl4AI wrapper, politeness, retry, prioritization
  extract/               12 pure extractors (meta, schema, qa, stats, …)
  nlp/                   LLM client (Ollama/cloud) + tone analysis
  scoring/               rubric loader, tier math, aggregator, scorers/ (one per criterion)
  processor/             Dual-Layer Gap Analysis (60% best-practice + 40% competitor)
  reference/             Reference Layer: blueprint, framework, domain config, config pinning
  recommender/           schema / entity / content edit generators
  validation/            recommend → simulate → re-score → retry (≤3) loop
  report/                per-page + site-level report builder, renderer, PDF export
  obs/                   Observability (agent traces, OpenTelemetry) + Error Sink
  storage/               psycopg2 pool, SQL migrations, repos (pages · runs · scores · jobs · …)
  pipeline/              Orchestrator (async), Worker (queue), Reference-Architecture cycle
config/                  scoring · framework · crawler · prioritization · extractors · entities · domains/
tests/                   365 offline tests + engineered fixtures
docs/                    ARCHITECTURE · DEPLOYMENT · VALIDATION · MIGRATION_V3_V4 · PIPELINE_EXPLAINED
Dockerfile · docker-compose.yml · .github/workflows/ci.yml
```

---

## Testing

```bash
pytest -q          # 365 tests, fully offline (no DB, browser, or Ollama)
ruff check src tests
```

The engineered fixtures in `tests/fixtures/` are tuned to land on known tiers (strong ≈ 44/50,
weak ≈ 18/50, glossary surfaces the `DefinedTerm` gap), so the scorer tests double as an executable
rubric spec. Coverage and benchmark details: [docs/VALIDATION.md](docs/VALIDATION.md).

---

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design rationale, module map, data flow, extensibility. |
| [docs/PIPELINE_EXPLAINED.md](docs/PIPELINE_EXPLAINED.md) | Walkthrough of the end-to-end pipeline. |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Local, Docker, and cloud deploy; CI/CD; monitoring; scaling; secrets; backups. |
| [docs/VALIDATION.md](docs/VALIDATION.md) | Rubric → implementation mapping, test coverage, benchmark. |
| [docs/MIGRATION_V3_V4.md](docs/MIGRATION_V3_V4.md) | v3 → v4 migration report and production-readiness review. |
| [aeo_architecture_v4.md](aeo_architecture_v4.md) | The v4 target spec (Reference Architecture Generator, Coverage Diff, Independent Validator). |
| [BETA_RELEASE_NOTES.md](BETA_RELEASE_NOTES.md) | v4.1 beta: onboarding, dry-run, OpenTelemetry. |

---

## Requirements

**Python ≥ 3.11** · **PostgreSQL ≥ 14** · **Playwright Chromium** · *(optional)* Ollama,
PageSpeed Insights API key.

---

## License

Internal project — © Securin. All rights reserved. Not for external distribution without permission.
