# Strategy Engine Design (Task 5 — AI strategic workspace)

**Date:** 2026-06-17. **Status:** designed, not yet built (LLM-heavy — warrants a reviewed pass).

## Goal

Evolve the Strategy tab from a flat priority-folder list of `StrategyAction`s into an
**AI-generated strategic workspace**: strategies clustered (by difficulty / business
maturity / expected impact / effort) into folders, each with a **README · Action Plan ·
Success Metrics · Dependencies · Execution Checklist**.

## Current state

`StrategyAction` (`intelligence/scenario.py`; TS mirror `web/lib/types.ts`) is flat
(title/detail/category/effort/priority/related_slugs), produced deterministically by
`route_scenario()` → six category builders → `_prioritize()`. The frontend `StrategyPanel`
buckets them into even-thirds High/Med/Low folders. An `LLMClient` is already threaded into
`build_site_profile` (used only as the business-model tiebreak today).

## Design (deterministic-first, mirrors Tasks 1+2 and `recommender/content.py`)

- **New module** `src/aeo/intelligence/strategy_cluster.py`:
  `cluster_strategies(plan, *, profile_ctx, llm) -> list[StrategyCluster]`.
  `StrategyCluster` = `{title, theme, readme, action_plan[], success_metrics[],
  dependencies[], checklist[{label, action_ids}], member_action_indices[]}`.
  `StrategyPlan` gains `clusters: list[StrategyCluster] = []`.
- Call it inside `route_scenario()` / `build_site_profile` **only when `llm and llm.enabled`**.
  One JSON LLM call: feed the deterministic `actions` + scenario / business_model /
  journey-gaps / score as grounding; validate/clamp via `generate_json`; **fall back to the
  existing 3-band buckets re-expressed as trivial clusters** so the UI has one render path.
- **Config**: a `strategy_cluster` block in `config/intelligence.yaml` (cluster cap, allowed
  axes, max checklist items) loaded via `config.py` — tunable without code.
- **Persistence**: clusters serialize inside `SiteProfile.to_dict()` under `"clusters"`, so
  they ride into `site_reports.sections["strategy"]` and the `plan_states.profile` snapshot
  — **no migration** (both JSONB). Deterministic `actions` stay as the source of truth;
  clusters reference actions by index.
- **Streaming**: emit a `strategy_cluster` stage via the existing `record_stage` sink during
  the audit. For the fast live `/api/profile` path, prefer **lazy** enrichment — return the
  deterministic profile immediately and have the Strategy tab fetch
  `/api/strategy-clusters` on open (progressive reveal, zero added time-to-first-result).
- **Frontend**: `SiteProfile.clusters?` → `StrategyPanel` renders cluster cards (collapsible
  README + the five sections; checklist items reuse the `plan_states.done` persistence) when
  present, else the current `bucketActions` view unchanged.

## Open questions

- Eager-on-`use_llm` vs lazy `/api/strategy-clusters` fetch (prefer lazy for snappiness).
- Checklist item identity: mint stable ids that join `plan_states.done_task_ids` (shared
  completion with the kit tab) vs per-cluster local checklist.
- Replace the High/Med/Low folders when clusters exist, or sit above them as a richer view?
- Caching: persist-and-reuse the LLM clustering by (domain, score-snapshot) to avoid
  re-clustering (cost/nondeterminism) on every live call.
