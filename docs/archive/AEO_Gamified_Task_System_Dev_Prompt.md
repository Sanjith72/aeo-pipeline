# Developer Build Prompt — Gamified AEO Task Tracker

## Context: what this system is for

This is not a generic gamification skin. The underlying data is real: a customer enters their industry, their optimization goals, and their content needs into our AEO (Answer Engine Optimization) platform. The system analyzes their site and generates a prioritized task list — things like "add FAQPage schema to product pages," "build a comparison page against Competitor X," "fix Core Web Vitals LCP," "get G2 reviews above the citation threshold," etc.

Each task already has, from the backend:
- A phase assignment (1, 2, or 3 — already prioritized by the recommendation engine)
- A title and description
- A completion criterion (binary: done / not done, or in some cases a percentage if it's measurable, e.g. "47/50 G2 reviews")
- An estimated impact score (how much this task is expected to move the citation/visibility needle)
- A category tag (technical, content, off-page, schema, etc.)

The job today is to design and build the **presentation layer** that turns this real, prioritized task list into the pirate-map game experience described below. The mechanics are not decorative — phase order, enemy escalation, and reward size must all map to the real priority and impact data coming from the backend. Nothing here is random or purely cosmetic; every visual signal should be traceable to a real property of the underlying task.

---

## High-level structure

The full task list is split into exactly **3 phases**, matching the backend's existing phase assignment:

- **Phase 1** — Highest priority tasks (the engine's top-impact, do-first items)
- **Phase 2** — Medium priority tasks
- **Phase 3** — Lower priority / cleanup tasks, intended to be done last

Each phase is rendered as a **collapsed accordion/dropdown tab** at the top level. Three tabs are visible by default, collapsed:

```
▸ PHASE 1 — [Phase title]              ████████░░ 80%
▸ PHASE 2 — [Phase title]              ███░░░░░░░ 30%
▸ PHASE 3 — [Phase title]              ░░░░░░░░░░ 0%
```

Each tab header always shows, even while collapsed:
- The phase number and name
- A persistent progress bar reflecting % of tasks completed in that phase
- A small coin icon + running coin total earned in that phase

Clicking a phase tab expands it into the full map view for that phase. Only one phase should be expanded at a time by default (accordion behavior), but allow the user to manually expand more than one if they want to compare — don't force-collapse others on click, just default to single-open on first load.

**Phase unlock logic:** Phase 2 and Phase 3 tabs are visible from the start (so the user can see the full journey ahead), but should be visually "locked" — dimmed, with a small padlock icon — until Phase 1 reaches 100% completion. Locked phases can still be expanded to preview the map and tasks inside (read-only, no interaction), but tasks inside a locked phase cannot be marked complete. This gives the user visibility into the full roadmap without letting them skip priority order. Once a phase hits 100%, animate the lock icon unlocking before allowing interaction on the next phase.

---



### Dynamic enemy scaling — important implementation detail

The backend will not always generate exactly 6 tasks per phase. The number of tasks per phase is dynamic based on the customer's actual audit results. The enemy roster above is a **6-step reference scale**, not a fixed array. Implement enemy assignment like this:

- Treat the enemy list per phase as an ordered difficulty scale from "weakest" to "final boss"
- If a phase has fewer than 6 tasks, compress the scale proportionally (e.g., 3 tasks in Phase 1 → deckhand, skeleton king, pirate fleet — skip the middle steps evenly, always keep the first as weakest and the last as the final boss)
- If a phase has more than 6 tasks, either (a) repeat/reskin mid-tier enemies with minor visual variation (palette swap, slightly different pose) to fill the gap, or (b) interpolate additional named enemies at design's discretion — but the final task in the phase must always render as the named final boss for that theme, never a generic enemy
- The **last task in any phase is always the final boss for that theme**, regardless of how many tasks precede it
- The **first task in any phase is always the weakest enemy** for that theme

Additionally: an enemy's *visual size/scale* on the map should also reflect that specific task's backend-provided impact score, independent of its position. A high-impact task that happens to be 2nd in sequence can render slightly larger/more imposing than the standard "2nd enemy" would otherwise be, within reason — don't let this override the ordering, just let it modulate scale ±15-20% so impact is legible at a glance even mid-sequence.

---

## Per-task states and interactions

Each task/node on the map needs to support these states, with a distinct visual treatment for each:

1. **Locked** (task not yet reachable — all tasks after the current active one): grayed out, enemy obscured in shadow/fog, node connected by a dashed/inactive line, not clickable
2. **Active/current** (the next task the user should do): enemy fully visible and "idle" animated (slight breathing/floating loop, not static), node glowing or highlighted, clickable — clicking opens the task detail panel
3. **Completed**: enemy defeated, node shows a checkmark/flag/banner planted, connected by a solid "cleared" line to the next node

### Task detail panel
Clicking an active node opens a panel (modal or side panel — your call) showing:
- Task title and description (from backend)
- Why it matters (short rationale, from backend if available, otherwise a generic category explanation)
- The action(s) required to mark it complete
- A "Mark Complete" button (or, if the task is auto-verified by the system — e.g., a schema check — a "Verify" button that pings the backend)

### Completion animation sequence
When a task is marked complete, play this sequence in order — do not skip or compress steps, each should be visually distinct and given enough time to register (rough timing suggestions, adjust to feel right):

1. **Defeat animation** (~0.6–1s): the enemy at that node plays a defeat animation — for small enemies this can be simple (a quick "poof"/knockback + fade), for boss-tier enemies make it more dramatic (longer animation, screen shake optional for final bosses only)
2. **Coin reward animation** (~0.8–1.2s): a burst of blue coins spawns from the node and arcs/flies up toward that phase's progress bar at the top of the screen. Coin count for this burst should scale with the task's impact score (small task = handful of coins, high-impact task = bigger visible burst) — this is the main way impact is communicated besides enemy size
3. **Progress bar update** (~0.4s): once coins reach the bar, the bar fills by the corresponding amount with a smooth easing animation, and the running coin total ticks up
4. **Unlock animation** (~0.5s): the dashed line to the next node solidifies, fog/shadow on the next enemy lifts, next node becomes active and starts its idle animation

Total sequence should feel satisfying but not slow — aim for the full chain to resolve in under 3 seconds so users doing several tasks in a row don't feel throttled. Consider letting users skip/speed up the animation on repeat completions (e.g., hold to skip) if this becomes a complaint in testing, but build the full sequence first.

### Final task in a phase — chest animation
When the last task in a phase is completed, after the standard 4-step sequence above plays out, trigger an additional **chest/vault/cargo-pod opening animation** specific to that phase's theme (treasure chest / vault door / cargo pod — see theme sections above). This should feel like the "big" moment — bigger coin payout than any single task in that phase, more dramatic animation, ideally a brief celebratory UI moment (confetti, screen flash, a "Phase Complete!" banner). This is also the trigger point for unlocking the next phase tab (see Phase unlock logic above).

---

## Progress bar and coin system

- Each phase has its own progress bar, visible at all times in that phase's tab header (collapsed or expanded)
- Progress bar fill % = (completed tasks in phase / total tasks in phase) — keep this simple and linear; don't weight it by impact score, that's already communicated via coin burst size and enemy scale
- Blue coins are cumulative and tracked both per-phase (shown in that phase's tab) and as a **global total** somewhere persistent in the main UI (header/nav — exact placement up to whoever owns the main app shell, just needs to be always visible, not buried)
- Coins are awarded only on task completion (per-task burst) and on phase completion (chest bonus) — no other source for now
- **Open question to confirm with product before building reward logic further:** do coins unlock anything functional (discounts, feature unlocks, leaderboard standing) or are they purely a visual progress/dopamine mechanic for now? Build the coin animation and counting system either way, but don't build any redemption logic until this is confirmed — treat coins as display-only state for this build.

---

## Technical notes

- Build this as a self-contained component that takes a `tasks` array as props/data (grouped by phase, each task with: id, phase, title, description, completionCriteria, impactScore, category, status) — don't hardcode the pirate/dungeon/space content as the only possible theme set; theme should be a config object keyed by phase so we can swap Phase 3's theme later without touching the component logic
- Enemy assets, background art, and chest animations should be swappable per theme via the same config — treat "theme" as a data-driven skin, not hardcoded markup
- Respect `prefers-reduced-motion` — provide a reduced-motion fallback for all animations (instant state changes with a simple fade instead of the full sequence) for accessibility
- All animations should be interruptible/skippable — don't trap the user in an unskippable cutscene if they complete tasks rapidly
- Mobile responsiveness: the map/path layout needs a mobile fallback — a horizontal winding path may not work well under ~480px width, consider a vertical path orientation below a breakpoint
- Keyboard accessibility: every node and the "Mark Complete" actions must be reachable and operable via keyboard, with visible focus states

---

## Summary of what to build, in order

1. Three-tab accordion shell (Phase 1/2/3), each with header showing name, progress bar, coin total, lock state
2. Map rendering component that takes a task list and theme config and lays out connected nodes
3. Enemy assignment logic (dynamic scaling per the rules above, including impact-based size modulation)
4. Task states (locked/active/completed) and the task detail panel
5. The 4-step completion animation sequence
6. Phase-complete chest animation + phase unlock trigger
7. Global + per-phase coin tracking (display only, no redemption logic yet)
8. Theme config system for Pirate / Dungeon / Space (and easy swap-out for Phase 3 if needed)
9. Reduced-motion, mobile, and keyboard accessibility passes

Flag any of the open questions above (Phase 3 theme finalization, coin utility) back to product before building deeper into those specific pieces — everything else in this spec is approved to build as written.
