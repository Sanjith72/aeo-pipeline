# Task Prioritization Architecture (Tasks 1 + 2)

**Date:** 2026-06-17. **Status:** deterministic baseline shipped; LLM-refinement seam designed.

## Goal

Turn the flat plan into **High / Med / Low priority folders** (only High open by default —
progressive disclosure), where each task carries `{priority, impact, difficulty, rationale,
recommendedNextAction}` the LLM can set, with a deterministic fallback so the fast path
never depends on the model.

## Where it lives (deterministic-first seam)

`build_plan` (`src/aeo/report/packager.py`) stays **pure/deterministic** (guarded by
`test_deterministic`). After it assembles the tasks, `_enrich_task` attaches the priority
signals:

- `priority_band` ∈ {high, med, low} — `high` when `quick_win` or `phase == week_1`, else
  `week_2_4 → med`, `later → low`.
- `impact` (1–5) — from the node `priority` float (`round(1 + priority*4)`), else by band.
- `difficulty` (1–5) — from `effort` (`low→1, medium→3, high→5`).
- `rationale` — short "why this matters" derived from band/quick-win.
- `recommended_next_action` — the concrete next step (`action_required`).

These ride inside the existing `StructuredPlan` (no schema/migration change) and persist
in `plan_states.plan` / `site_reports`.

## Frontend (`web/components/results.tsx`)

`PhasedPlanView` flattens the plan and groups tasks by `priority_band` into collapsible
`TaskFolder`s (`TASK_BANDS`: High/Med/Low), **only High `defaultOpen`**. The progress bar
and "Today" tray (next ≤3 quick wins) stay pinned above. `TaskCard`'s drawer shows
"Why this matters" + `Impact n/5 · Effort n/5`. `web/lib/types.ts:PlanTask` gained the
optional fields.

## The LLM refinement pass (designed, not yet built — Task 2's "use the LLM")

Add `enrich_plan_with_llm(plan, *, business, topic, llm)` called from `plan_for(..., llm=)`
(thread `llm` from `/api/deliverables`, already in scope). One batched `LLMClient.
generate_json` over the deterministic task list returns per-id `{priority_band, impact,
difficulty, rationale, recommendedNextAction}`. Rules:

- **Run only when `llm.enabled`** (keep the fast default path deterministic).
- **Clamp to the deterministic signal** — don't let the model contradict `effort`/`priority`
  by more than one band (avoid a thin utility page marked "high").
- **Fall back to the deterministic fields on any error/malformed JSON** (mirror
  `recommender/draft.py`'s bounded, failure-safe pattern).
- Keep it **outside `build_plan`** so the determinism invariant + existing tests hold.

## Open questions (carried from the investigation)

- `recommendedNextAction` currently equals `action_required`; if the LLM pass adds distinct
  value, differentiate the copy in the card.
- Bands vs. the old phase grouping: bands are now the kit's primary grouping; phase is still
  on each task and could power a "by timeline" toggle if wanted.
