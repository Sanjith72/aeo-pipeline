# Phase 4 — Validation & Rubric Mapping

This document proves that all ten AEO rubric criteria are implemented, maps
each one to the code that scores it, summarises test coverage, reports a
benchmark, and logs the optimisations applied during validation.

The rubric is **config, not code**: `config/scoring.yaml` is the single source
of truth for thresholds, weights, and vocabularies. `config/extractors.yaml`
holds the regex packs and CSS selectors. Tuning the rubric never requires a code
change.

---

## 1. Criterion coverage — 8 / 8

Every criterion in the Securin AEO rubric has a deterministic scorer registered
in `src/aeo/scoring/scorers/__init__.py`. The registry keys are a hard contract:
`storage/repos/scores.py` indexes results by these exact names to fill the
`rubric_scores_v2` columns.

| # | Rubric criterion          | Registry key         | Scorer module                              | Implemented |
|---|---------------------------|----------------------|--------------------------------------------|:-----------:|
| 1 | Schema Markup             | `schema_markup`      | `scoring/scorers/schema_markup.py`         | ✅ |
| 2 | Q&A Blocks                | `qa_blocks`          | `scoring/scorers/qa_blocks.py`             | ✅ |
| 3 | Stats in HTML             | `stats_in_html`      | `scoring/scorers/stats_in_html.py`         | ✅ |
| 4 | Entity Consistency        | `entity_consistency` | `scoring/scorers/entity_consistency.py`    | ✅ |
| 5 | Heading Structure         | `heading_structure`  | `scoring/scorers/heading_structure.py`     | ✅ |
| 6 | Content Depth             | `content_depth`      | `scoring/scorers/content_depth.py`         | ✅ |
| 7 | Citation Signals (E-E-A-T)| `citation_signals`   | `scoring/scorers/citation_signals.py`      | ✅ |
| 8 | Load Speed                | `load_speed`         | `scoring/scorers/load_speed.py`            | ✅ |
| 9 | Render Accessibility      | `render_accessibility` | `scoring/scorers/render_accessibility.py` | ✅ |
| 10 | Answer Readability       | `answer_readability` | `scoring/scorers/answer_readability.py`    | ✅ |

Criteria 9–10 were added in v3 (the rubric expanded 8 → 10).
Max score = 10 × 5 = **50**. Priority tiers (`scoring/result.py`):
`critical` < 35 %, `high` < 55 %, `medium` < 75 %, `low` ≥ 75 % of max.

---

## 2. Rubric → implementation mapping

How each criterion turns raw HTML into a 1–5 tier. "Mode" is `deterministic`
(pure parse, no network/LLM) or `hybrid` (deterministic base, LLM refines when
`AEO__LLM__ENABLED=true`; degrades to deterministic-only when Ollama is absent).

| Criterion | Extractor(s) consumed | Config keys (`scoring.yaml`) | Scoring logic | Mode |
|-----------|-----------------------|------------------------------|---------------|------|
| **Schema Markup** | `schema_jsonld`, `glossary` | `valued_types` | Tier by count of high-value JSON-LD types present (0 blocks→1, no valued→2, 1→3, 2→4, 3+→5); −1 for malformed blocks. Surfaces glossary `DefinedTerm` gap as evidence. | deterministic |
| **Q&A Blocks** | `qa_blocks`, `schema_jsonld` | `min_answer_chars`, `question_words` | Tier by count of real question→answer pairs (0→1, 1→2, 2→3, ≤4→4, 5+→5); +1 tier if `FAQPage` schema present. | deterministic |
| **Stats in HTML** | `stats` (+ LLM disqualifier) | `tiers {1:0,2:1,3:3,4:6,5:10}` | Count distinct concrete numeric claims → threshold tier. LLM (when on) re-counts *genuine* statistics and the lower count wins. | hybrid |
| **Entity Consistency** | `entities` | `tiers {1:0,2:.5,3:1,4:1.5,5:2.5}` | Ratio of canonical-entity mentions to first-person ("we/our/us"). Zero first-person → top tier; never names entity → floor. | deterministic |
| **Heading Structure** | `headings` | `tiers (question ratio)`, `penalty_missing_h1`, `penalty_template_h1` | Base tier from share of H2/H3 that are questions; −1 each for missing H1 and template H1 ("Resources"/"Welcome"/…). | deterministic |
| **Content Depth** | `readability`, `stats`, `headings`, `chunker`, `meta` (+ tone, LLM) | `min_word_count_for_credit`, `methodology_keywords` | Length-based base, +1 for methodology language or ≥6 stats, −1 for promotional + thin; LLM returns an independent 1–5 averaged in. | hybrid |
| **Citation Signals** | `eeat`, `links`, `schema_jsonld` | `authority_domains`, `credentials`, compound `tiers` | Highest tier whose author / date / authority-link-count requirements are all met. Author & date fall back to JSON-LD. | deterministic |
| **Load Speed** | `pagespeed`, `render_mode` | `tiers (PSI score)`, `penalty_js_only_content` | PSI mobile performance → tier; **neutral 3** when PSI unavailable (not punitive); −1 for JS-only content. | deterministic |

**Fault isolation:** `run_all()` wraps every scorer in try/except. A scorer that
raises floors to `scale_min` with `scored_by="error"` and the error in evidence,
so one bad criterion never aborts a page (verified by
`TestRunAllIsolation`).

---

## 3. Test coverage

**240 tests, all passing, fully offline** (LLM disabled in `conftest.py` before
import; no DB, browser, or Ollama). Deterministic across `PYTHONHASHSEED`
(verified over 8 seeds).

| Module | Focus |
|--------|-------|
| `tests/unit/test_helpers.py` | Pure functions: tier clamping/rounding, threshold mapping, priority buckets, promotional-tone detection, text utils. |
| `tests/unit/test_extractors.py` | Raw signal contract each extractor emits, asserted on three engineered fixtures. |
| `tests/unit/test_scorers.py` | The executable rubric spec: strong page = **44/50 (low)**, weak page = **18/50 (high)**, glossary surfaces the `DefinedTerm` gap; full PSI→tier mapping; fault isolation. |

The three fixtures (`tests/fixtures/*.html`) are tuned to land on known tiers, so
the scorer tests double as a regression guard on the whole extract→score path.

Run: `python -m pytest -q` (config: `pyproject.toml` sets `pythonpath = ["src"]`,
so no editable install is needed).

---

## 4. Benchmark

Extract + score over each fixture, 200 iterations, single core, LLM disabled,
Python 3.14.3, `lxml` parser. Excludes network fetch, PageSpeed API, and LLM —
those are I/O-bound and run concurrently in the pipeline.

| Page | Extract | Score | Total | Throughput |
|------|--------:|------:|------:|-----------:|
| strong (526 w, rich schema) | 27.4 ms | 4.7 ms | **32.1 ms** | ~31 pg/s/core |
| weak (54 w) | 14.3 ms | 0.8 ms | **15.1 ms** | ~66 pg/s/core |
| glossary (277 w, 22 terms) | 29.3 ms | 0.1 ms | **29.4 ms** | ~34 pg/s/core |

Extraction dominates; scoring is sub-5 ms. In production the real ceiling is the
headless-browser fetch (~1–5 s/page), so CPU scoring cost is negligible and
throughput scales with crawl concurrency (`crawler.concurrency`).

---

## 5. Optimisation log

### Applied during validation

1. **Cache `load_yaml_file()` (`settings.py`).** It re-read and re-parsed YAML
   from disk on *every* call; six extractors call it 1–2× per page (eeat twice),
   i.e. **8 disk reads + YAML parses per page**. `get_settings()` and
   `load_rubric()` were already `@lru_cache`'d — this one was missed. Config is
   static for a process, so caching is safe. **Result: ~3× speedup** (strong
   92→32 ms, weak 68→15 ms, glossary 88→29 ms).

2. **Deterministic authority-domain matching (`extract/links.py`).** Matching
   iterated `authority_domains` as a `set` and broke on the first hit. A link to
   `nvd.nist.gov` matches both `nist.gov` and `nvd.nist.gov`, so the recorded
   label depended on hash-seed-randomised set order — a **flaky test** (failed
   ~1 in 5 runs). Now attributes to the **most specific (longest)** configured
   domain, which is order-independent. Stable across 8 seeds.

3. **Money-stat regex suffix order (`config/extractors.yaml`).** The alternation
   `(?:k|m|b|million|billion)` matched `b` before `billion`, so "$1.2 billion"
   was captured as "$1.2 b". Reordered longest-first
   `(?:million|billion|k|m|b)`. Doesn't change the stat *count*, only the stored
   value string.

### Known limitations (documented, not fixed — they need design changes)

- **JS-only-content detection is inert (`extract/render_mode.py`).** It compares
  raw-HTML text length to `body_text(soup)` length, but both derive from the
  *same post-render HTML* the pipeline passes in, and `body_text()` strips
  nav/footer/header — so `initial_len ≥ rendered_len` always, making
  `inflation ≤ 1.0` and `js_only` impossible. The `load_speed` JS penalty only
  fires from injected data (as in `TestLoadSpeedScoring`). **Fix:** capture a
  pre-JS snapshot (plain `httpx` GET) alongside the rendered DOM and compare the
  two. Pipeline change — slated for a future iteration.

- **Credential false positives (`extract/eeat.py`).** `CISA` is both a
  certification and the agency name, so "CISA KEV catalog" registers as a
  credential. **Impact: cosmetic** — `credentials_mentioned` is evidence only;
  `citation_signals` scores on author/date/authority-links, not credential
  count. **Fix (optional):** require a proximity context
  (`certified|certification|holds`) near the match.

### Future opportunities (not blocking)

- **Parse once per page.** Each extractor gets a fresh `BeautifulSoup` because
  `body_text()` is destructive (~21 ms of repeated parsing on the strong page).
  A non-destructive `body_text()` (operate on a copy / collect text without
  `decompose()`) would let all 12 extractors share one parse.
- **Pre-compile stats regexes once** instead of per-call (now cheap relative to
  parsing, but trivial to hoist to module level).
