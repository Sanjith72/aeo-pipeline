# AEO Crawler — v4.1 Beta (Monday)

Implements the **FINAL DECISION** from `architecture_comparison.md`: harden Architecture A
(this repo) with the genuinely-better, low-risk components from Architecture B (Sanjith's
`aeo-pipeline`). No merge, no rewrite — A stays the system of record; five B ideas are
transplanted onto it.

**Status: ready for beta.** `385 passed, 6 skipped` (DB-gated integration), `ruff` clean.
Test count grew **364 → 385** (21 new). Every feature is additive and settings-gated; the
existing v4 contract is unchanged.

---

## What's new (the five transplants)

### 1. Per-domain YAML onboarding — `config/domains/{domain}.yaml`
Onboard a client with a file, not a code/env change. Overrides `topic`, `engine_target`,
`max_urls`, and `label` for that domain's runs. Ships with `config/domains/securin.io.yaml`
(PEV / perplexity / 150). Resolution order: explicit CLI arg → domain config → settings →
framework default.
- Code: `src/aeo/reference/domain_config.py`; wired in `pipeline/reference_arch.py` +
  `pipeline/orchestrator.run_site`. Tests: `tests/unit/test_domain_config.py` (6).

### 2. `--dry-run` in-memory preview — `aeo audit DOMAIN --dry-run`
Previews what an audit would surface — discovered pages, the ideal-site blueprint, and the
**site-level coverage gap (missing/thin pages)** — with optional per-page scoring, and
**writes NOTHING to the database**. Ideal for onboarding a new domain or a stakeholder demo.
- `--pages N` (default 5): crawl + score this many top pages in memory (`0` = structural only).
- `--llm/--no-llm` (default off): use the LLM for blueprint synthesis.
- Code: `Orchestrator.dry_run` (`pipeline/orchestrator.py`) + CLI flag. Tests: `tests/unit/test_dry_run.py` (2),
  including a guard asserting no run/DB write ever happens.

### 3. OpenTelemetry OTLP export — alongside `agent_traces`
Standards-aligned distributed tracing for a collector (Tempo/Jaeger/Honeycomb), running
**next to** the existing queryable `agent_traces` table + `aeo trace` (kept as-is). Off by
default; a **hard no-op** when the SDK isn't installed or no endpoint is set, and
exception-proof (never breaks a run).
- Enable: `AEO__OBS__OTEL_ENABLED=true`, `AEO__OBS__OTEL_ENDPOINT=http://collector:4317`,
  and `pip install -e ".[otel]"`.
- Code: `src/aeo/obs/otel.py`; hooked into `obs/tracing.trace_step`. Settings: `ObsCfg`.
  Tests: `tests/unit/test_otel.py` (5).

### 4. Config pinning per run — week-over-week comparability
The scoring-contract configs (`framework.yaml`, `best_practices.yaml`, `scoring.yaml`,
`prioritization.yaml`) are fingerprinted and folded into the blueprint's version hash. Editing
the measuring stick now **bumps the blueprint version** — so a score jump reads as "new
baseline," not "real change." Fixes the "configs not pinned per run" risk from the review.
- Code: `src/aeo/reference/config_pin.py` + `Blueprint.config_fingerprint` (in `hash_inputs`).
  Tests: `tests/unit/test_config_pin.py` (4).

### 5. Engine-target prompt routing — *already shipped, now configurable per domain*
Engine emphasis (Perplexity / ChatGPT-Search / Gemini / generic) was already wired into
blueprint synthesis (`reference/generator.py`). v4.1 lets each domain pick its engine via the
onboarding YAML, so it actually varies per client.

### Bonus beta-hardening
- **Windows console fix**: `aeo --help` no longer crashes on cp1252 terminals (Unicode `→` in
  help text). UTF-8 is forced on stdout/stderr at CLI startup (`src/aeo/cli.py`).
- **CI**: a new `migrations` job spins up Postgres 16 and runs `aeo migrate` + the DB-gated
  integration tests, closing the "first deploy is the first real migration" gap
  (`.github/workflows/ci.yml`).

---

## v4.2 additions — works for ANY website + PDF deliverables

### 6. Any-website genericization
The per-page rubric was always topic-agnostic; the only Securin-specific piece was
`framework.yaml` (the ideal-site taxonomy driving blueprint + coverage). Now any site
gets its own:
- `aeo framework bootstrap <domain>` writes `config/domains/<domain>.framework.yaml` — a
  deterministic universal ideal-site skeleton (homepage, product, solutions, pricing,
  resources/FAQ cluster, about, contact); `--llm` tailors it to the site's real topic
  (entities, topic clusters, seed questions), re-validated against the closed vocabulary.
- `load_framework(domain)` auto-picks up that per-domain file; the blueprint topic now
  comes from the framework, not the global default.
- Code: `reference/framework_bootstrap.py`, `reference/framework.py` (domain-aware).
  Tests: `tests/unit/test_framework_bootstrap.py` (8).

Onboard any site:
```
aeo framework bootstrap acme.com            # generic; add --llm to tailor (needs Ollama/Gemini)
#   review/edit config/domains/acme.com.framework.yaml
aeo add-target Acme acme.com
aeo audit-cycle acme.com -t Acme
```

### 7. PDF export of the deliverables
- `aeo report <scope> --pdf out.pdf` → client-ready per-page reports (one page each).
- `aeo site-report -r <run> --pdf out.pdf` → site coverage + net-new-content PDF.
- Optional dep: `pip install -e ".[pdf]"` (reportlab). Code: `report/pdf.py`. Tests: `tests/unit/test_pdf.py` (3).

## Explicitly NOT in this beta (roadmap, per the review)
- **psycopg2 → asyncpg** async-DB refactor — large/risky; deferred. A's sync-DB-in-async wart
  remains, with the migration path documented in the deployment diagram.

---

## Demo script for Monday

```bash
# 0. setup (once)
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"            # add ".[otel]" to demo OpenTelemetry export
python -m playwright install chromium

# 1. Onboarding + dry-run preview — NO database needed, nothing written
aeo audit securin.io --dry-run --pages 5
#   -> discovered/selected counts, ideal-site blueprint (15 nodes, engine=perplexity from
#      config/domains/securin.io.yaml), site-level coverage % + top missing pages, and
#      in-memory per-page scores. "db_writes": 0.

# 2. Full audit against ANY website (needs DATABASE_URL + Playwright chromium)
aeo migrate                                # create the schema (once)
aeo add-target Acme acme.com               # register the site (once per site) — NEW
aeo audit-cycle acme.com -t Acme           # weekly loop, end to end (blueprint→coverage→crawl→score→analyze→site report)
aeo report Acme                            # view the per-page deliverables

# 3. (optional) OpenTelemetry
#   set AEO__OBS__OTEL_ENABLED=true and AEO__OBS__OTEL_ENDPOINT=http://localhost:4317
#   spans for every pipeline step export to your collector, alongside `aeo trace PAGE_ID`.
```

## Verification done in this build
- `ruff check src tests` — clean.
- `pytest -q` — **385 passed, 6 skipped** (the 6 are DB-gated integration tests; run them with
  a live Postgres via the new CI `migrations` job or locally with `DATABASE_URL` set).
- `aeo audit --help`, `aeo --help` — render without error on Windows cp1252.
- Onboarding + blueprint generation verified end-to-end offline (securin.io → PEV / perplexity
  / 150; blueprint 15 nodes with a config fingerprint).
- **Not verified here** (need live creds/services): a live Crawl4AI crawl, live Gemini/Perplexity,
  and the DB round-trips — all behind injectable seams, unit-tested with fakes.
