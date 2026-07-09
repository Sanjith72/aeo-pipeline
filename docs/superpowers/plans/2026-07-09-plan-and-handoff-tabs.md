# Plan: collapse Roadmap + Strategy into "Plan" + "Handoff"

**Status:** approved, not started. No code has been changed.
**Date:** 2026-07-09
**Decision:** direction **C + E**, with A's view toggle folded in, and ordering made **advisory**.

---

## The problem

`Roadmap` and `Strategy` are two tabs rendering the same list. Both are facets of one
mounted `TrackerView` driven by one `useQuestTracker` instance
(`web/components/quest/TrackerView.tsx:48`). Strategy renders `dash.milestones` raw; Roadmap
renders `buildQuestModel(plan, dash)`, a pure projection of the same `dash`. They cannot
disagree, because there is only one list.

The comment at `web/components/results.tsx:302` explains the origin: *"Strategy = the single
actionable list (the old Roadmap + Strategy tabs merged)."* Roadmap was merged into Strategy
once; the quest map then took the vacated name. This is migration debt, not a design.

Three consequences worth naming:

1. **The tab named "Strategy" contains no strategy.** `PlanTask` carries `impact_score`,
   `effort`, `quick_win` (`web/lib/types.ts:100`). `MilestoneTask` does not
   (`web/lib/types.ts:137`). Only the Roadmap consumes those fields — to size coins and
   enemies. The non-gamified tab is an unranked checklist.

2. **The game's constraints are unenforceable.** Roadmap gates tasks sequentially and locks
   phases (`web/lib/quest/model.ts:130-147`). Strategy lets you set any task to any status at
   any time, through the *same* `tracker.setStatus`. Mark three tasks verified on Strategy,
   return to Roadmap, phase unlocked. Every lock is one tab-click from void.

3. **There is a second, unrelated gamification engine** (`GamificationStrip`,
   `/api/gamification`, `companion/rewards.py`) that the results page never renders.

---

## Correction to earlier analysis

I previously told you that a single verified fix increments "verified wins" in the strip *and*
banks coins on the map — one event, two currencies. **That was wrong.** Reading the backend:

|                     | Persisted "verified win"                          | Quest coin banking                                    |
| ------------------- | ------------------------------------------------- | ----------------------------------------------------- |
| Source table        | `recommendation_outcomes` (status `implemented`)   | `milestone_tasks` (`verified_completed` + `crawl`)     |
| Written by          | `outcomes.mark_from_recrawl` (audit orchestrator)  | `milestones.mark_verified` (weekly `milestone_audit`)  |
| Verification test   | rubric **criterion tier rose** vs pinned baseline  | **artifact exists live** (page / service / heading)    |
| Identity key        | `session_id`                                       | `client_id`                                            |
| Surface             | `GamificationStrip`, `/studio` results only        | coins in `quest/model.ts`, `/plan/[id]` only           |

`src/aeo/verification/milestone_verify.py:15` says the divergence is deliberate: *"This is
intentionally NARROWER than the Retention Engine's hash check… we detect existence, we don't
re-grade quality."*

Two implications for this plan:

- The engines are **not** joinable today. They aren't even keyed to the same identity. Fully
  unifying them is a backend project, not a UI refactor.
- `GamificationStrip` renders **only** in `StudioApp`'s results view
  (`web/components/StudioApp.tsx:679`). It does **not** render on `/plan/[id]`.

So "make the game ambient" splits into a cheap half and an expensive half. This plan does the
cheap half and defers the expensive one. (Also noted while reading: `rewards.maturity()` can
never return `cited_leader` — the top branch returns `authority` — and `citations_earned` is
never incremented. Pre-existing, out of scope, flagged so it isn't mistaken for our regression.)

---

## Target information architecture

| Tab                 | Job                | Contents                                                                 |
| ------------------- | ------------------ | ------------------------------------------------------------------------ |
| Overview            | judge it           | audit findings, `FixImpact`, **+ "Bigger strategic moves"** (moved here)  |
| Your website plan   | (unchanged)        | blueprint                                                                 |
| **Plan**            | do it              | Quest Map ⇄ List toggle, site check, coin bank hoisted to header          |
| **Handoff**         | delegate it        | share link, per-task dev briefs, copy-all / export                        |

`Roadmap` and `Strategy` both disappear as names. Coins and checkpoints move above the tab bar
so they follow the user across every tab.

---

## Two structural moves that make the rest easy

### Move 1 — lift the tracker to `ResultsView`

`useQuestTracker` currently lives in `TrackerView`, which is buried inside `PlanPanel`, which
is inside the tab panel. Nothing above it can read coins.

Lift the hook to `ResultsView` (`results.tsx:295`), which already holds `deliverables`,
`domain`, and `businessName`, and expose it via a small context provider so `PlanPanel`,
`TrackerView`, the new `HandoffPanel`, and a new header coin strip all read one instance.

Guard for the no-domain case: `useQuestTracker` requires a domain, and the `PhasedPlanView`
fallback (`results.tsx:1137`) has none. The provider returns `null` and the coin strip renders
nothing — the same `if (!s) return null` discipline `GamificationStrip.tsx:33` already uses.

### Move 2 — render *both* views from `QuestModel`, not from `dash`

`buildQuestModel` already joins `MilestoneTask` → `PlanTask` on `task_key === id`
(`model.ts:82`), so it has `impact_score` for every task. `MilestoneDashboard` currently reads
`tracker.dash` and throws that away.

Point `MilestoneDashboard` at `tracker.model` instead. The list view gains impact ranking,
coin values, and the "recommended next" marker **for free, with no backend change**, and both
views converge on one view-model. This is the single highest-leverage line in this plan.

Caveat: the read-only `/share/[token]` dev view renders from server data with no `plan` in
hand, so it cannot build a `QuestModel`. If we want impact there too, `impact_score` /
`effort` / `quick_win` must be persisted onto `milestone_tasks` — a migration. **Deferred**;
not needed for anything in Phases 1–3.

---

## Phase 1 — advisory ordering (do this first, standalone)

Ships independently and fixes the real bug. Nothing else depends on it, and it shrinks the
surface everything after it touches.

**`web/lib/quest/model.ts`**
- Delete the lock cascade (`L130-136`). Remove `QuestPhase.locked`.
- Rewrite the node-state pass (`L140-147`). `NodeState` becomes
  `"available" | "recommended" | "completed"` (`web/lib/quest/types.ts:7`).
  - `recommended` = first incomplete task **in that phase** (one per phase, for local guidance).
  - Add `QuestModel.nextTaskId` = the first incomplete task in the first incomplete phase — the
    one globally-true "do this next", for a header CTA.
- Leave `isComplete`, `chestBonus`, and all coin math untouched.

**`web/components/quest/PhaseMap.tsx`**
- `L271` `readOnly={live.nodeState === "locked"}` → drop the prop entirely.

**`web/components/quest/TaskDetailPanel.tsx`**
- Delete the `readOnly` prop (`L42`) and its branch (`L172-177`), including the string
  **"Defeat the enemies before this one on the trail first."** Always render the action buttons.

**`web/components/quest/EnemyNode.tsx`**
- Replace the `state === "locked"` branches: remove the 🔒 padlock badge (`L78-82`) and the
  `opacity-40 grayscale blur-[1px]` treatment (`L62`).
- `available` = normal. `recommended` = the glow ring currently on `active` (`L46`), plus a
  subtle pulse.
- `ariaState` (`L27-35`): `"locked"` → `"available"` / `"recommended next"`.

**`web/components/quest/PhaseTab.tsx`**
- Remove the `wasLocked` / `justUnlocked` latch (`L39-51`, `L63`), the `🔒 Locked` pill
  (`L92-96`), the `🔓 Unlocked!` beat (`L97-106`), and the lock text in `aria-label` (`L78`).
- Keep the phase progress bar and coin tally.

**`web/components/quest/QuestMap.tsx`**
- `L80` default-open phase: `find((p) => !p.isComplete)` (drop the `!p.locked` clause).

**Tests — `web/lib/quest/model.test.ts`**

These encode the blocking cascade and must be rewritten, not deleted:
`L133-152`, `L154-171`, `L189-211` (esp. `L200`, "no active node inside a locked phase"),
`L213-223`, `L313-324`.

New assertions: nothing is ever `locked`; exactly one `recommended` per incomplete phase; zero
`recommended` in a complete phase; `nextTaskId` points at the first incomplete task of the
first incomplete phase; a zero-task phase still doesn't break the walk.

**Design note.** We're removing a coercive beat (the unlock) and must not leave a hole. The
compensating beats already exist: the phase-complete finale, the chest, the 25/50/75/100
checkpoints, and now the `recommended` glow. Coins persuade; gates no longer block. This is the
whole argument for advisory ordering — the product operates on someone's real website, and if
their developer has Tuesday free, "defeat the enemies before this one" is a hostile answer.

**Bug 2 dissolves by construction:** there is no lock left for the list view to bypass.

---

## Phase 2 — the view toggle (Plan tab)

**`web/components/results.tsx`**
- Tab list (`L296-307`): drop the `strategy` entry. Rename `actions` → label **"Plan"**.
- `L332` `const planFacet = tab === "actions" ? "map" : "strategy"` — delete. The facet now comes
  from user state, not tab state.
- `L331` `planVisible` simplifies to `tab === "plan" || tab === "kit"`.

**`TrackerFacet`** keeps its two values but changes owner: a `viewMode` state in `PlanPanel`,
persisted to `localStorage` under the existing `storageKey` convention
(`results.tsx:345`, `aeo-plan:${…}`). Default **Map** — it's the differentiator; a returning
user who picked List keeps List.

**`web/components/quest/TrackerView.tsx`**
- Already mounts both facets and toggles `hidden` (`L61-81`) — that machinery is exactly right
  for a toggle and needs no change. Only the *source* of `facet` changes.
- Delete the intro paragraph (`L70-75`) pointing at the "Roadmap" tab.
- Remove `StrategyExtras` and `DeveloperHandoffPanel` from this component (they move; see below).

**`web/components/MilestoneDashboard.tsx`**
- Switch from `tracker.dash` to `tracker.model` (Move 2). Render an impact pill and the
  `recommended` marker. Keep the 3-state `StatusControl` — free ordering is now the sanctioned
  behavior in both views, not an escape hatch.

**Segmented control** lives in the Plan panel header, next to "Check my site now": `Map | List`.

---

## Phase 3 — the Handoff tab

**Relocations (v1 is mostly moving what exists):**
- `DeveloperHandoffPanel` (`MilestoneDashboard.tsx:152`) → the new `web/components/HandoffPanel.tsx`.
- Per-task dev briefs: the `DeveloperHandoff` tab inside `TaskHowTo` (`TaskHowTo.tsx:224-291`)
  becomes the Handoff tab's per-task body. `TaskHowTo`'s `showDeveloper` prop
  (`TaskHowTo.tsx:61`) can then default to `false` and the Plan tab stops carrying dev content.
- **Do not move `raw_snippet` out of the DIY tab.** It is a paste-ready artifact a
  non-technical owner genuinely uses in their CMS. It should appear in *both* places.

**Net-new (this is what earns the tab):**
- "Copy all open tasks as Markdown" — a single paste-ready checklist of every `dev_brief`.
- Download as `.md`.
- Filter by status; count of open tasks in the tab label or header.

**Deferred to v2:** export to GitHub Issues / Linear / Jira. Worth doing, but v1 must not block
on picking an integration.

**`web/components/results.tsx`**
- Add `{ id: "handoff", label: "Handoff" }` to the tab list, gated on `profile && domain`
  (it needs a tracker, same as Plan).

**"Bigger strategic moves"** (`StrategyExtras`) has no home once Strategy dies. It reads
`profile.actions` deduped against the plan (`TrackerView.tsx:56`), touches no tracker state, and
is pure diagnosis. **Move it to the Overview tab**, which already hosts audit-derived content.
This also pre-builds the slot for direction B later (Strategy-as-thesis). Fallback if Overview
gets crowded: bottom of the Plan tab's List view.

---

## Phase 4 — ambient gamification (the cheap half of E)

Coins are a pure function of `dash + plan`, already computed by `buildQuestModel`. With Move 1
done, the coin bank needs **no backend work at all**.

- Extract the coin-bank header from `QuestMap.tsx:147-157` into `web/components/CoinBank.tsx`.
- Render it in `ResultsView` **above the tab bar**, fed by the lifted tracker.
- Move the checkpoint-crossing effect (`QuestMap.tsx:91-107`) and `CheckpointModal` up with it,
  so a 50% checkpoint fires while the user is reading Overview.
- Keep the per-phase coin tally in `PhaseTab.tsx:130-135` — that's local, not ambient.
- Nit while you're in there: `QuestMap.tsx:93` divides by `model.maxCoins` with no zero guard.
  Benign today (`NaN > n` is `false`, so no modal fires), but guard it.

**Explicitly out of scope: unifying the two reward engines.** That means reconciling
`recommendation_outcomes` with `milestone_tasks`, and `session_id` with `client_id`. It's a real
backend project with its own plan. Until then, `GamificationStrip` stays on `/studio` and the
coin bank stays on `/plan/[id]`, and we accept that the product has two honest-but-separate
notions of a verified win.

---

## Copy that must change

| Location | Current | Note |
| --- | --- | --- |
| `TaskDetailPanel.tsx:176` | "Defeat the enemies before this one on the trail first." | delete (Phase 1) |
| `TaskDetailPanel.tsx:164-168` | "…lives on the **Strategy** tab…" | → the Handoff tab |
| `TrackerView.tsx:70-75` | "Prefer the journey view? That's the **Roadmap** tab." | delete (it's a toggle now) |
| `PhaseTab.tsx:92-96` | "🔒 Locked" | delete |
| `EnemyNode.tsx:33-34` | aria "locked" | → "available" / "recommended next" |
| `results.tsx:299-306`, `326-330` | comments describing the two-facet tab scheme | rewrite |
| `TrackerView.tsx:3-12`, `useQuestTracker.ts:3-7`, `MilestoneDashboard.tsx:3-9` | header comments naming the Roadmap/Strategy tabs | rewrite |

---

## Analytics

- `TabId` values are emitted in events. Renaming `actions` → `plan` and dropping `strategy`
  breaks continuity in existing dashboards. Either keep the `actions` id and change only the
  label, or rename and record the cutover date. **Recommend renaming** — the ids are a lie
  otherwise — and annotating the dashboards.
- `quest_task_status` vs `milestone_task_status` (`QuestMap.tsx:61`, `MilestoneDashboard.tsx:52`)
  currently distinguish tabs. Keep both: they now distinguish *view modes*, which is more useful.
- Add `plan_view_toggled { to: "map" | "list" }`. It answers the question this whole refactor is
  premised on — does anyone actually want the list?
- Keep `quest_checkpoint_reached`; it now fires from `ResultsView`, not `QuestMap`.

---

## Risks

- **Lifting the tracker is the risky change**, not the tab renames. `TrackerView` deliberately
  keeps facets mounted so tab switches don't re-`syncMilestones` (a DB write) or re-fire
  analytics (`TrackerView.tsx:11-12`). The lifted provider must preserve that: one `useEffect`,
  keyed on `[domain, businessName, plan, cmsType]`, mounted once at `ResultsView`.
- Removing the unlock beat may measurably reduce engagement. `plan_view_toggled` and
  `quest_checkpoint_reached` are the instruments to watch. If it dents, the answer is a stronger
  `recommended` affordance, not restoring the gates.
- `MilestoneDashboard` switching to `tracker.model` makes it depend on `plan` being present.
  Confirm every path that renders it has one — `/share/[token]` in particular, which I have not
  read.

## Sequencing

Phase 1 ships alone and is worth shipping alone. Phases 2 and 3 both depend on Move 1 and should
land together, since Phase 2 deletes the Strategy tab and Phase 3 is where its contents go.
Phase 4 is independent of 2 and 3 once Move 1 exists.
