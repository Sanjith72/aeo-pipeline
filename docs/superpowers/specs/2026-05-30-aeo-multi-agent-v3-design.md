# AEO Multi-Agent v3 — Design Spec

- **Date:** 2026-05-30
- **Status:** Approved (design); ready for implementation planning
- **Source of truth:** `D:\CyclaaraAI_vault\Second_Brain\raw-sources\securin\aeo_architecture_v3.md` (frozen v3 diagram)
- **Final deliverable:** an optimized **per-page AEO/SEO report** for a target site (Securin first), produced end-to-end as one runnable product.

---

## 1. Context

The Crawler block already exists and is shipped (`src/aeo/`): site discovery, 12 extractors, **8** deterministic scorers, a custom async orchestrator + Postgres job queue, and a gated Ollama LLM client. This spec covers building the remaining v3 blocks so the system runs as a single unit: **Processor → Recommender → Reference Layer → Validation → Report**, plus the cross-cutting **Observability** and **Error Sink** utilities.

The system stays true to the principles the shipped code already follows: **deterministic-first** (parse HTML → score; LLM only refines), **config-not-code** (thresholds/weights/vocab in `config/*.yaml` at repo root), **pure functions**, **failure isolation**, **Postgres as backend**. New principle added here: **provider-agnostic LLM**.

## 2. Resolved decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM strategy | **Hybrid, config-switched** | Ship both adapters; default Ollama in dev (free/offline), cloud API in prod (quality). Flip via `AEO__LLM__PROVIDER`. |
| Orchestration | **Extend existing async orchestrator + Postgres queue** (not LangGraph) | Proven/tested code, minimal deps, fits deterministic-first; keep a clean seam so LangGraph can be swapped later if branching grows. |
| Scope | **Full system, end-to-end, built incrementally** | User goal: "works as an entire unit… made into a product… one by one." |
| Named Processor agents | **Merge Entity Check / Citability / Tech-Accessibility into the 10-criterion rubric as criteria** (not separate re-scoring agents) | Avoids duplicate/conflicting scoring; the frozen doc itself flags the overlap as something to "discover by measuring." |
| Teammates' blocks (Reference Layer, Prioritization, Observability) | **Build provisional versions now**, behind clean interfaces | So the system runs end-to-end today; Aayush/Sanjith refine later by changing only the loader/impl, not the consumers. |

## 3. The 10-criterion rubric (8 → 10 reconciliation)

The frozen doc says "expand rubric **4 → 10**," but the shipped system already has **8** scorers. So the real change is **8 → 10**: keep all 8 existing registry keys (hard contract) and add 2 new scorers that wrap extractors that already exist but are not yet scored (`render_mode`, `readability`, `chunker`). The doc's separate "Entity Check / Citability / Tech-Accessibility" agents map onto criteria, not new modules.

| # | Criterion (registry key) | Status | Backing extractor(s) | Doc agent it satisfies |
|---|---|---|---|---|
| 1 | `schema_markup` | have | schema_jsonld | — |
| 2 | `qa_blocks` | have | qa_blocks | — |
| 3 | `stats_in_html` | have | stats | — |
| 4 | `entity_consistency` | have | entities | **Entity Check** |
| 5 | `heading_structure` | have | headings | — |
| 6 | `content_depth` | have | readability/chunker | (LLM-refined) |
| 7 | `citation_signals` | have | eeat/links | **Citability** |
| 8 | `load_speed` | have | pagespeed | **Tech Accessibility** (perf) |
| 9 | `render_accessibility` | **new scorer** | render_mode | **Tech Accessibility** (JS-only content is invisible to answer engines) |
| 10 | `answer_readability` | **new scorer** | readability + chunker | — |

The "Analyzer" is the existing `run_all` + aggregator over 10 criteria; the LLM refines only `content_depth` and `stats`. No new extraction code is required — only 2 scorers + 2 `config/scoring.yaml` blocks + 1 migration note + tests.

## 4. Component design

### 4.1 Page Prioritization (Crawler block)
`src/aeo/crawl/prioritize.py` — pure functions:
- `classify(url) -> PageType` from URL patterns (homepage/product/solution/pillar/blog/about/contact/utility).
- `rank(pages, traffic) -> list[ScoredUrl]`: `base_weight(type) × traffic_signal`, where traffic signal is internal-link count now (GSC export later), then cut to top **N** (default 30).
- Weights + N live in `config/prioritization.yaml`. Output = ordered top-N URLs the per-page loop processes; full ranking persisted for observability.

### 4.2 Dual-Layer Gap Analysis (Processor block)
`src/aeo/processor/gap_analysis.py` — deterministic-first:
- **60% best-practice gap:** per-criterion `max(0, target − actual)` using targets from the Reference Layer, weighted by the rubric weights.
- **40% competitor gap:** compare against the best competitor page for the same query intent (competitors are already crawled and scored through the same 10-criterion rubric).
- Output: `overall_gap`, ordered `criterion_gaps[]` (the prioritized deficiency list), and an optional LLM narrative. Feeds the Recommender. Replaces deleted legacy `gap_analysis_agent.py` logic, now scoring-driven.

### 4.3 Recommender block
`src/aeo/recommender/`:
- `schema.py` — **deterministic**: generate the JSON-LD the page is missing (FAQPage, Article, Organization, BreadcrumbList) from extracted content. Templating, no LLM.
- `content.py` — **LLM**: concrete content edits (add FAQ, add stats, restructure headings), grounded in Best-Practice snippets injected into the prompt.
- `entity.py` — **LLM**: entity additions/corrections for consistency with `config/entities.yaml`.
- All three grounded by the Reference Layer.

### 4.4 Reference Layer (provisional; Aayush refines)
`config/best_practices.yaml` + `src/aeo/reference/` loader exposing typed accessors:
- Per-criterion **target scores** (the 60% baseline for gap analysis).
- **Content Architecture Framework:** ideal structure per page-type.
- **Query Intent** classifier (lightweight URL+heading heuristic; informational/commercial/navigational), marked conceptual.
Seeded from the existing rubric + cybersecurity AEO norms. Consumers (gap analysis, recommender) depend only on the accessor interface, so a richer future version (e.g. vector-backed) changes only the loader.

### 4.5 Validation + Human Review
`src/aeo/validation/validator.py`:
- Apply proposed edits to a **synthetic page**, **re-score through the same 10-criterion rubric**, compare to original.
- Improved → mark for Human Review. Not improved → retry the Recommender (feeding back the failed attempt), capped at **3** (config). After 3 failures → flag "could-not-improve" in the report → Human Review.
- "Human Review" is a `review_status` DB flag + a report section, **not** a UI.

### 4.6 Utilities (cross-cutting; Sanjith refines)
- **Orchestrator:** extend the existing async orchestrator; per-page pipeline = Analyze → Gap → Recommend → Validate(≤3) → Report, each step isolated by the Error Sink, durable via the queue. New job types added.
- **Observability** `src/aeo/obs/`: per-agent/per-page structured logs (extend structlog binding) + an `agent_traces` table (run_id, page_id, agent, step, status, duration_ms, model, tokens, error) written by every step + an `aeo trace <page>` CLI to dump a page's journey. Per-criterion scores already persisted.
- **Error Sink** `src/aeo/obs/error_sink.py`: one helper every block calls on failure → writes failed-status row + trace + log, then signals "skip page, continue run." Generalizes the existing `run_all` floor-on-error and `crawl_status='failed'`. **One bad page never kills a run.**

### 4.7 Provider-agnostic LLM interface
Generalize `LLMClient` (in `src/aeo/nlp/llm.py`) into an `LLMProvider` protocol (`generate`, `generate_json`; still returns `None` on failure to preserve isolation). Adapters: `OllamaProvider` (today's code) + `CloudProvider` (one OpenAI-compatible HTTP adapter — covers OpenAI, Gemini's compat endpoint, and most vendors). `get_client()` reads `settings.llm.provider` (`ollama` | `cloud`). Per-environment via existing `AEO__LLM__PROVIDER`.

## 5. Data model (new migrations)
- `0004_prioritization.sql` — `page_priorities` (run_id, url, page_type, base_weight, traffic_signal, final_rank, selected).
- `0005_gap_and_recs.sql` — `gap_analyses` (page_id, run_id, bestpractice_gap, competitor_gap, overall_gap, detail JSONB); `recommendations` (page_id, type, payload JSONB, status, attempt, validated, score_before, score_after).
- `0006_reports.sql` — `page_reports` (page_id, run_id, summary, sections JSONB, review_status, generated_at).
- `0007_observability.sql` — `agent_traces` (id, run_id, page_id, agent, step, status, duration_ms, model, tokens, error, created_at).

## 6. Build order (dependency-correct, incremental)
- **A — Foundations:** provider-agnostic LLM interface; migrations 0004–0007; Observability + Error Sink helpers. *(Unblocks everything; low risk.)*
- **B — Reference Layer (provisional):** `best_practices.yaml` + loader + query-intent classifier. *(Gap analysis's 60% layer depends on it.)*
- **C — Processor depth:** 2 new scorers → 10-criterion rubric; Analyzer wrapper; Dual-Layer Gap Analysis.
- **D — Page Prioritization:** classifier + ranker + cutoff. *(Parallelizable.)*
- **E — Recommender:** schema (deterministic) → content + entity (LLM, grounded).
- **F — Validation loop:** re-score simulation, retry ≤3, flag.
- **G — Report + Human-Review status + CLI** (`aeo report <target>`, `aeo trace <page>`).
- **H — Orchestrator wiring** end-to-end + new job types + worker; run the thin end-to-end slice on **Securin**.

## 7. Non-goals (YAGNI)
- No web UI / dashboard (Human Review = DB flag + report section).
- No LangGraph now (seam preserved for later).
- No vector DB for the Reference Layer now (YAML + loader; swappable).
- No new extractors (the 2 new scorers reuse existing extractors).

## 8. Open questions — discover by running (from the frozen doc)
- Right N for prioritization (10/30/50?).
- Right validation retry threshold (start at 3).
- Whether Entity Check should stay a rubric criterion or split back out (measure overlap).
- Exact shape of the Best Practices Reference (Aayush's research).

These are intentionally deferred: the frozen doc says build the thinnest end-to-end slice on one site, then iterate.
