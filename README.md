# AEO Crawler

Crawl, extract, and score web pages against an **Answer Engine Optimization
(AEO)** rubric for cybersecurity content. Built to audit Securin and competitor
pages on the ten criteria that determine how well content surfaces in AI
answer engines.

Scoring is **deterministic-first**: all ten criteria score from parsed HTML
signals alone. A local LLM (Ollama) is optional and only *refines* content depth —
disable it and you still get a complete, reproducible 0–50 score.

---

## The rubric — 10 criteria, scored 1–5 (max 50)

| # | Criterion | What it measures |
|---|-----------|------------------|
| 1 | **Schema Markup** | High-value JSON-LD types (FAQPage, TechArticle, …); flags the glossary `DefinedTerm` gap. |
| 2 | **Q&A Blocks** | Real question→answer pairs; bonus for `FAQPage` schema. |
| 3 | **Stats in HTML** | Distinct concrete numeric claims (percentages, money, CVE/CVSS…). |
| 4 | **Entity Consistency** | Brand/entity mentions vs. first-person ("we/our") language. |
| 5 | **Heading Structure** | Question-phrased H2/H3s; penalties for missing/template H1. |
| 6 | **Content Depth** | Length, methodology language, stats, promotional tone (LLM-refined). |
| 7 | **Citation Signals (E-E-A-T)** | Author, date, and authoritative external links. |
| 8 | **Load Speed** | PageSpeed Insights mobile score; JS-only-content penalty. |
| 9 | **Render Accessibility** | Is the answer in the server-rendered HTML (not JS-injected)? Penalizes high render-inflation and JS-only content. |
| 10 | **Answer Readability** | Flesch reading ease, sentence length, and passage segmentation — can an engine lift a clean answer? |

Criteria 1–8 are the shipped contract; **9–10 were added in v3** (the rubric
expanded 8 → 10). The rubric lives in [`config/scoring.yaml`](config/scoring.yaml) — thresholds,
weights, and vocabularies are **config, not code**.

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate   # Win: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium

cp .env.example .env            # set DATABASE_URL
aeo migrate                     # create the schema
aeo run https://securin.io/blog/some-post -t Securin
```

Prefer containers? `docker compose up -d db && docker compose run --rm migrate &&
docker compose up -d worker`. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### CLI

```
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

# v4 Reference Architecture
aeo audit-cycle DOMAIN -t N  weekly loop: blueprint → coverage → crawl → analyze → site report
aeo blueprint generate       generate (or reuse) the versioned ideal-site blueprint
aeo blueprint show           print a stored blueprint (ideal sitemap + coverage map)
aeo coverage    -r RUN_ID    site-level Coverage Diff (missing / thin pages)
aeo site-report -r RUN_ID    render the site-level AEO report
aeo refinements [--propose]  validated-wins criteria-target proposals (human-gated)

# v4.1 (beta) — onboarding · dry-run · OpenTelemetry
aeo audit DOMAIN --dry-run   in-memory preview (discover → blueprint → coverage [+ score]); writes NOTHING to the DB
#  onboarding : drop config/domains/<domain>.yaml to set topic / engine_target / max_urls / label per client
#  OTEL       : AEO__OBS__OTEL_ENABLED=true + pip install -e ".[otel]" exports OTLP spans alongside agent_traces
#  config pin : the scoring-contract configs are folded into the blueprint version hash (week-over-week comparability)
```

---

## How it works

```
URLs ─► Crawl (Crawl4AI/Playwright) ─► Extract (12 pure extractors)
                                              │
                                              ▼
                        Score (10 deterministic scorers, LLM optional)
                                              │
                                              ▼
                    PostgreSQL: pages · extractions · rubric_scores_v2
```

`aeo audit <domain>` prepends **Site Discovery** (sitemap + recursive) and
**Page Prioritization** (rank by value, cut to the top-N) so the per-page loop
spends its budget only on the highest-value URLs. The back half —
`aeo analyze` — runs Dual-Layer Gap Analysis → Recommender → Validation (≤3
retries) → per-page report, with an Error Sink isolating each page and an
Observability layer (`agent_traces`, `aeo trace`) recording every step.

- **Extractors** are pure `extract(html, soup, url) -> dict` functions. Each gets
  a fresh `BeautifulSoup` (text extraction is destructive by design).
- **Scorers** read a single `ScoreContext` and map raw signals to a 1–5 tier
  using thresholds from the rubric. One scorer failing never aborts a page —
  it floors and records the error.
- **PostgreSQL** is both the job queue (`FOR UPDATE SKIP LOCKED`) and the result
  store. No external broker.

**v4 adds a topic layer on top of the page layer.** Once per run, the **Reference
Architecture Generator** builds a versioned *blueprint* — the ideal sitemap +
coverage map for a topic — by combining competitor structural patterns (L1), a
curated framework (L2, `config/framework.yaml`), and optional Gemini synthesis
(L3). The **Coverage Diff** measures the site against that blueprint ("which pages
are missing"), and the **Independent Validator** re-checks recommendations against
*non-circular* signals (liftable TL;DR, H1-as-question, valid JSON-LD) plus a
Perplexity citation test — fixing v3's circular validation. `aeo audit-cycle`
chains the whole thing for the weekly loop. See
[docs/MIGRATION_V3_V4.md](docs/MIGRATION_V3_V4.md).

Full design rationale and module map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Project structure

```
src/aeo/
  cli.py                 typer CLI (entry point: `aeo`)
  settings.py            layered config (defaults → YAML → env)
  logging.py             structlog setup
  crawl/                 site discovery, Crawl4AI wrapper, politeness, retry,
                         fingerprinting, page prioritization
  extract/               12 pure extractors (meta, schema, qa, stats, …)
  nlp/                   LLM client (Ollama/cloud) + tone analysis
  scoring/
    rubric.py            loads config/scoring.yaml
    result.py            tier math (clamp, thresholds, priority)
    aggregator.py        score_page() — weighted total
    scorers/             one module per criterion (10)
  processor/             Dual-Layer Gap Analysis (60% best-practice + 40% competitor)
  reference/             Reference Layer: best-practice targets, page architecture,
                         query-intent classifier
  recommender/           schema / entity / content edit generators
  validation/            recommend → simulate → re-score → retry (≤3) loop
  report/                per-page report builder + renderer (the deliverable)
  obs/                   Observability (agent traces) + Error Sink (page isolation)
  storage/
    db.py                psycopg2 connection pool
    migrate.py           SQL migration runner
    migrations/          *.sql (idempotent, ordered)
    repos/               pages · runs · extractions · scores · jobs · targets ·
                         priorities · gaps · recommendations · reports · traces
  pipeline/              Orchestrator (async), Worker (queue), stages, analysis
config/                  scoring.yaml · extractors.yaml · entities.yaml · crawler.yaml ·
                         prioritization.yaml · best_practices.yaml
tests/                   346 offline tests + 3 engineered fixtures
docs/                    ARCHITECTURE · DEPLOYMENT · VALIDATION
Dockerfile · docker-compose.yml · .github/workflows/ci.yml
```

---

## Testing

```bash
pytest -q          # 346 tests, fully offline (no DB, browser, or Ollama)
ruff check src tests
```

The three fixtures in `tests/fixtures/` are tuned to land on known tiers
(strong = 44/50, weak = 18/50, glossary surfaces the DefinedTerm gap), so the
scorer tests double as an executable rubric spec. Coverage and benchmark:
[docs/VALIDATION.md](docs/VALIDATION.md).

---

## Documentation

| Doc | Contents |
|-----|----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design rationale, module map, data flow, extensibility, maintenance. |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Local, Docker, and cloud deploy; CI/CD; monitoring; scaling; scheduling; secrets; backups; cost. |
| [docs/VALIDATION.md](docs/VALIDATION.md) | Rubric→implementation mapping, test coverage, benchmark, optimization log. |
| [docs/MIGRATION_V3_V4.md](docs/MIGRATION_V3_V4.md) | **v4** migration report: what changed v3→v4, the final architecture, production-readiness review, remaining risks. |
| [aeo_architecture_v4.md](aeo_architecture_v4.md) | The v4 target spec (Reference Architecture Generator, Coverage Diff, Independent Validator, …). |

## Requirements

Python ≥ 3.11 · PostgreSQL ≥ 14 · Playwright Chromium · *(optional)* Ollama,
PageSpeed Insights API key.
