# Claude Code prompt — verified-coin gating + checkpoint celebration

Paste everything below the line into Claude Code from the repo root.

---

Implement two changes to the gamified Quest Map (the Roadmap tab). **Both are frontend-only — do not touch the backend, database, or API.** Every field you need is already on `MilestoneTask` (`verify_kind`, `status_source`) and already surfaced in the quest model.

## Context (read these first)

- `web/lib/quest/model.ts` — pure projection that builds the quest view model. `globalCoins` is the "coins earned" total shown in the Roadmap header. Currently it sums `coins` for **every** task with `status === "verified_completed"`, regardless of how it got there.
- `web/lib/quest/types.ts` — `QuestTask` / `QuestPhase` / `QuestModel` shapes.
- `web/components/quest/QuestMap.tsx` — renders the header (`<Tally value={model.globalCoins} />`, label "coins earned"), the "Check my site now" button, and an existing per-phase `Confetti`. Note the `didInit` ref pattern used to avoid firing effects on the initial mount/sync.
- `web/components/quest/TaskDetailPanel.tsx` — the "Mark complete" action and the post-completion message ("Marked complete — coins earned").
- `web/components/quest/PhaseMap.tsx` — maps node clicks to `onStatus(task.id, "verified_completed")` (mark) and `"in_progress"` (working on it).
- `web/components/quest/CoinBurst.tsx` and `web/components/motion/primitives` (`m`, `AnimatePresence`, `Tally`, `useReducedMotion`) — reuse these; don't add new animation libraries.

### Key domain constraint — do not break this
`src/aeo/intelligence/milestone_verify.py` treats tasks with `verify_kind: "manual"` (off-site visibility wins) as **never auto-verifiable**. So "coins only on crawl verification" cannot be a blanket rule — off-site tasks would never pay out. The banking rule below handles this explicitly.

## Feature 1 — coins bank only when verified (pending → banked)

Replace the coin-award logic so a task banks its coins only when **either**:

1. it's crawl-verified: `status === "verified_completed" && statusSource === "crawl"` (i.e. the existing `verifiedLive`), **or**
2. it's an off-site task self-reported done: `status === "verified_completed" && milestoneTask.verify_kind === "manual"`.

A **crawlable** task (`verify_kind` of `page` / `service` / `heading`) that has only been manually marked is **pending**: it still counts for progress and phase unlock, but its coins do **not** count until the crawl confirms it.

Required changes:

- `types.ts`: add `coinsBanked: boolean` to `QuestTask`; add `maxCoins: number` to `QuestModel` (total earnable across the plan = sum of every task's `coins` + every phase's `chestBonus`).
- `model.ts`:
  - Add a `banked(task)` predicate implementing the rule above; set `task.coinsBanked`.
  - `coinsEarned` per phase and `globalCoins` must sum **banked** coins only.
  - Bank the `chestBonus` only when **every** task in the phase is banked (not merely `isComplete`).
  - Keep phase **unlock/lock cascade and `isComplete` on `status === "verified_completed"` as they are today** — progression stays on mark-complete; only coins move to the banked rule.
  - Compute and return `maxCoins`.
- `TaskDetailPanel.tsx`: replace the single "Marked complete — coins earned" line with three states:
  - crawl-verified → `Verified live on your site ✓ — coins banked`
  - off-site self-reported (`verify_kind === "manual"`, done) → `Self-reported ✓ — coins banked`
  - crawlable, marked but not yet verified → `Marked done — verify your site to bank {coins} coins.` (use the pending/emerald styling distinction; pending should read as amber/neutral, not the banked emerald)
- `PhaseMap.tsx`: give marked-but-not-banked nodes a subtle "pending verification" treatment (e.g. muted/dashed ring) so the map visually matches the coin total. Keep it minimal and on-palette.

## Feature 2 — checkpoint celebration every 25% of coins

When banked coins cross a checkpoint, show a celebratory pop-up. Use **percentage milestones at 25 / 50 / 75 / 100%** of `model.maxCoins` (always exactly four, always reachable regardless of plan size). Do **not** hard-code 500/1000/1500/2000.

- New component `web/components/quest/CheckpointModal.tsx`: a dedicated, centered celebration modal (bigger than the per-phase `Confetti`), built with the existing motion primitives. Show which checkpoint was reached (e.g. "Halfway there — 50%") and the current coin total. Respect `useReducedMotion`: when reduced, render **no** modal (the header `Tally` still ticks, so the reward isn't lost).
- In `QuestMap.tsx`:
  - Track the previous banked-coin milestone index in a ref. A checkpoint fires when `Math.floor(pct / 25)` increases, where `pct = 100 * globalCoins / maxCoins`.
  - **Guard the initial mount**: the model syncs 0 → current on load; do not fire checkpoints for that first jump. Reuse the `didInit` pattern already in this file.
  - If a single "Check my site" verify crosses **multiple** milestones at once, show **only the highest** reached (one modal).
  - Fire an analytics event consistent with the existing `api.track(...)` calls (e.g. `quest_checkpoint_reached` with `{ pct, coins }`).

## Constraints & conventions

- Frontend only. No backend/DB/API changes. No new dependencies.
- Match the existing code style: functional components, the `m` / `AnimatePresence` / `Tally` primitives, Tailwind core utilities, on-palette colors (accent, emerald for verified/banked, amber/neutral for pending — no off-palette yellow).
- `model.ts` must stay pure and runnable under `node --test` (no React/DOM imports).

## Verification (do this at the end)

1. Update/extend the `node --test` unit tests in `web/lib/quest` for: the banking predicate (crawl-verified banks, manual off-site banks, crawlable-but-manual is pending), chest-bonus banking only when the phase is fully banked, and `maxCoins`.
2. Run the quest model tests, then the typecheck and lint for `web/`.
3. Confirm no `verified_completed` filter remains for coin math and that phase unlock still works on mark-complete.

Show me a summary of the diff and the test results when done.
