# Gamified Quest Map — design (2026-06-25)

A pirate-map / dungeon / space **game view** over the existing implementation tracker.
It is a *second presentation* of the same milestone data the `MilestoneDashboard` (List
view) already renders — not a parallel system.

## Decisions (locked with the owner)

| Area | Decision |
|---|---|
| Scope | Self-contained presentational component **+ adapter** wired into the live tracker as a **List ⇄ Map** toggle in `results.tsx` (`PlanPanel`). Reuses all server status/verify/handoff plumbing. No new endpoints. |
| Rewards | **Hybrid (honest + responsive).** Manual "Mark Complete" defeats the enemy + drops coins immediately; a crawl-verified completion adds a distinct **"Verified live ✓" badge + bonus burst**. |
| Visual build | **SVG/CSS + icon (emoji) enemies + framer-motion.** No art-asset pipeline. Fully data-driven theme config. |
| Impact data | **Real `impact_score`** added to the backend `build_plan` / `PlanTask`. |
| Phase-3 theme | Space/Sci-Fi (spec default) — swappable via config. Flagged to product, not blocking. |
| Coins | **Display-only** (no redemption logic). Derived from status + impact — no new persistence. |

## Data foundation (already in the repo)

- `StructuredPlan` → exactly 3 `PlanPhase`s keyed `week_1` / `week_2_4` / `later`.
- `MilestoneDashboard` (from `api.syncMilestones(plan)`) → 3 milestones, **`milestone_key === phase key`**, each task carrying `status` (`pending|in_progress|verified_completed`) + `status_source` (`manual|crawl`).
- Join key: **`MilestoneTask.task_key === PlanTask.id`** (`page:<slug>` / `vis:<x>`).
- The Map view lives only where the `StructuredPlan` is in hand (owner contexts: `PlanPanel` with a `domain`). The read-only `/share/[token]` dev view keeps the List.

## Backend change — `src/aeo/report/packager.py`

Add `impact_score: float` (0–1) to `_page_plan_task` via a pure helper
`_impact_score(priority, effort, quick_win)` (anchored on the engine's `priority`, with a
quick-win bonus + a cornerstone/high-effort uplift, floored at 0.15), and an explicit value
per `_VIS_PLAN_TASKS` entry (GBP ≈ 0.95, reviews ≈ 0.85, listings ≈ 0.70, readthrough ≈ 0.25).
Mirror the field into the TS `PlanTask`. Extend `tests/unit/test_packager.py`.

## Adapter — `web/lib/quest/` (pure, unit-tested)

`buildQuestModel(plan, dashboard) → QuestModel` joins by `task_key === plan.id`:

- `nodeState`: `completed` iff `status === verified_completed`; `active` = first
  non-completed task in the phase (by position); `locked` = non-completed tasks after it.
  Handles out-of-order completion (a defeated enemy mid-trail just shows defeated).
- `enemyTier`: roster is an ordered difficulty scale; first task = weakest, **last task =
  named final boss always**; middle compressed/expanded by count.
- `enemySize`: `clamp(base ± impact·0.2)` so impact is legible mid-sequence.
- Coins (display-only, derived — no storage): `coins = round(BASE + impact·RANGE)`;
  `phase.coinsEarned = Σ completed coins (+ chest bonus at 100%)`; `globalCoins = Σ phases`.
- Phase lock: phase N is locked (preview-only, non-interactive) until phase N-1 is 100%.
  Already-earned progress in a locked phase still renders as defeated (never hidden).

## Completion semantics → existing 3-state model

| Milestone state | Enemy |
|---|---|
| `pending` | idle / not engaged |
| `in_progress` | engaged (struck, amber) |
| `verified_completed` + `manual` | defeated + coins (no "live" badge) |
| `verified_completed` + `crawl` | defeated + "Verified live ✓" badge + bonus burst |

## Components — `web/components/quest/`

- `QuestMap.tsx` — 3-phase accordion shell + global CoinBank; owns data via `useQuestTracker`.
- `useQuestTracker.ts` — sync on mount, optimistic `setStatus`, `verify` (mirrors the List view's data ownership; the List view is left untouched/de-risked).
- `PhaseTab.tsx` — header (name, progress bar, coin total, lock) + collapsible body + unlock anim.
- `PhaseMap.tsx` — SVG winding path + nodes; **vertical under ~480px**; orchestrates the 4-step completion FX + coin burst.
- `EnemyNode.tsx` — enemy (emoji sized by impact), `locked`/`active`/`completed` visuals.
- `TaskDetailPanel.tsx` — focus-trapped dialog: title, why, actions, Mark Complete / Verify; reuses the shared `TaskHowTo`.
- `web/components/TaskHowTo.tsx` — the how-to / DIY / Developer-handoff panel extracted from `MilestoneDashboard.tsx` so both views share one implementation.
- Theme config `web/lib/quest/theme.ts` — pirate / dungeon / space as data-driven skins (palette classes, ordered emoji roster, chest glyph, labels).

## Animation (4-step, interruptible, reduced-motion aware)

defeat → coin burst (count ∝ impact) arcs to the bar → bar fill + total tick → next node
unlock. Phase's last task adds a chest/vault/cargo finale + "Phase Complete!" banner + next
phase unlock. Built on the repo's framer-motion vocabulary (`m`, `useReducedMotion`,
`MotionConfig reducedMotion="user"`); reduced-motion collapses to instant state + fade.
Skippable (Esc / rapid completion fast-forwards).

## Accessibility / mobile

Nodes are real `<button>`s (Enter/Space); detail panel is a focus-trapped dialog;
`role="progressbar"` bars; locked nodes `aria-disabled`; visible focus rings (global
`:focus-visible`). Vertical path on mobile.

## Testing

- `web/lib/quest/model.test.ts` (node `--test`): enemy scaling (compress/expand, boss always
  last, weakest always first), coin math, node states, out-of-order completion, phase lock.
- `tests/unit/test_packager.py`: `impact_score` present, in range, deterministic.
- Manual: drive the app, complete a task + a phase, watch the chest fire.
