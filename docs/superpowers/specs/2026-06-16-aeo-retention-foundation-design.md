# AEO Retention Foundation — Design (Spec #1 of Approach B)

**Status:** approved, building. **Date:** 2026-06-16. **Branch:** `feat/aeo-retention-foundation`.

This is spec #1 of the "retention loop & moat" bet (Approach B). It builds the
foundation every later retention feature depends on, plus one visible win, so the
release is *felt* (a score that persists + a non-overwhelming board) rather than pure
plumbing. The verified re-crawl (the moat, B3), level-up celebrations / a rising
self-checked score (B4-full), and the weekly email (B5) are explicitly **out of scope**
and become their own specs.

## Why

The research workflow's diagnosis: the product wins the click but loses the human.
Plan progress lives only in `localStorage` keyed by business name — device-locked, lost
on cache-clear, invisible to the `return_visit` metric — so there is no object to return
*to*. And the plan renders all phases stacked at once, which reads as a 30-day dump and
invites deferral. This spec fixes both.

## Scope

| ID | Sub-project | What it delivers |
|----|-------------|------------------|
| **B0** | Canonical AEO Score | one pure `score()` over `SiteProfile`, reused on every surface |
| **B1** | Persisted + resumable plan | server-stored progress + profile/plan snapshot + a `/plan/<id>` link; cookie auto-resume |
| **B2** | "Today" tray + focused phases | a do-now surface of ≤3 quick wins; later phases collapsed, not dumped |
| **B4-lite** | Score ring | a circular gauge at the top of results: current score + a ghosted "where the plan gets you" target |

## Architecture & data flow

```
                        web/lib/score.ts  (pure, no deps)
                        aeoScore(profile) -> 0..100
                        aeoScoreCeiling(profile) -> target
                        scoreBand(n) -> {label, verdict, tone}
                                  |
  plan generated (page.tsx) ------+--> POST /api/plan-state --> repos/plan_state.py --> plan_states (0012)
        {session_id, name, domain, run_id?, plan, profile, score}
        returns {id} -> history.replaceState('/plan/<id>') + save-link affordance

  toggle a task --> PUT /api/plan-state/<id> {done_task_ids, score}   (+ existing task_marked_done event)
                    (fire-and-forget; localStorage mirror so a check is never lost)

  revisit homepage --> GET /api/plan-state?session_id=  --> "Resume your plan ->" banner
  open shared link  --> GET /api/plan-state/<id>        --> /plan/[id] renders standalone
```

## B0 — Canonical AEO Score · `web/lib/score.ts` (new, pure)

Weighted blend of inputs already on `SiteProfile`; weights are named constants in one
place. Any component whose signal is absent (denominator 0) is dropped and the remaining
weights renormalized, so a thin profile never reads artificially low for missing data.

- `foundation = classification.structure_score` — weight .40
- `journey = covered_stages / total_stages` — .35
- `presence = present / (present + missing) archetypes` — .15
- `confidence = business_intent.confidence` — .10
- `aeoScore = round(100 * weightedSum)` — always a 0–100 int, never `NaN`.

`aeoScoreCeiling(profile)` recomputes with journey & presence at 1.0 and a sound
foundation (confidence left as-is — it's how well we understand the business, not
something the plan changes); never below the current score. It's the ghosted target arc.

`scoreBand(n)` → `{label, verdict, tone}` for four bands (Barely visible / On the radar /
Recommended / Top answer). The labels become the named level tiers in B4-full.

**Decision:** the headline number is **purely `SiteProfile`-derived** — the same formula
on the fast profile and on the richer post-audit profile, so it refines naturally. The
rubric `avg_pct` (deep-audit pages) is a *different scale* and is deliberately **not**
mixed in (the "two numbers for one site" trust trap).

## B1 — Persisted + resumable plan

**Migration `0012_plan_states.sql`** (additive, idempotent; mirrors `0011`): table
`plan_states(id TEXT PK, session_id, run_id FK->crawl_runs ON DELETE SET NULL,
business_name, domain, plan JSONB, profile JSONB, score_snapshot INT, done_task_ids JSONB,
created_at, updated_at)` + index `(session_id, updated_at DESC)` + the shared
`set_updated_at` trigger.

**Repo `src/aeo/storage/repos/plan_state.py`** (mirrors existing repos): `new_id()`
(unguessable `secrets.token_urlsafe`), `create(...)`, `get(id)`, `update_progress(id,
done[], score?)` (only writes score when provided — a progress save never clobbers the
issue-time score with null), `latest_for_session(sid)`.

**Endpoints (`app.py`, thin delegates):** `POST /api/plan-state` → `{id}` ·
`GET /api/plan-state/{id}` → row | 404 · `PUT /api/plan-state/{id}` (progress) ·
`GET /api/plan-state?session_id=` → `{id|null}`. CORS `allow_methods` gains `PUT`.

**Frontend:** `api.ts` adds `createPlanState`/`getPlanState`/`updatePlanState` (fire-and-
forget)/`resumePlan` (reads the session cookie internally). On plan generation, `page.tsx`
POSTs plan+profile+score, stores the id, and `history.replaceState`s the address bar to
`/plan/<id>` (no remount). New route **`web/app/plan/[id]/page.tsx`** fetches the row and
renders `ResumedPlanView` standalone so the link works on a fresh device. A
`usePlanProgress` hook replaces the localStorage block in `PhasedPlanView`: server is the
source of truth when a `planStateId` exists, localStorage is the offline mirror/fallback.
On homepage load, a best-effort `resumePlan()` shows a dismissible "Welcome back — resume
your plan →" banner.

**Decisions:** the minted token is the durable artifact (works for the no-website path,
which has no `run_id`; shareable; device-independent). The session link enables same-
browser auto-resume. The token is unguessable; a plan holds only public site analysis, so
link-readable is acceptable. **Generated ZIP assets are not persisted** (large, LLM-built)
— the resumed view focuses on the interactive plan; re-downloading routes back to the
studio.

## B2 — "Today" tray + focused phases · `web/components/results.tsx`

- `TodayTray`: the next ≤3 incomplete `quick_win` tasks (quick wins first, then by phase,
  then priority) as prominent one-tap rows; refills as they complete. Falls back to the
  active phase's next task when quick wins run dry; hides at 100%.
- Phases render **focused**: the active phase (first with an incomplete task) is open;
  later phases collapse behind a muted "Coming up — N tasks" line with a **"Show
  everything"** escape (focus, not a hard gate — "you can start anytime").
- Phase titles relabeled **client-side** by `phase.key` → "Do these now / This week /
  Once you're rolling"; backend packager untouched.

**Decision (in scope):** also default the results tab to the plan (`kit`) when a plan
exists, so the felt outcome (a do-now board) isn't buried under the diagnosis dashboard.

## B4-lite — Score ring · `ScoreRing` at the top of `ResultsView`

SVG gauge + `CountUp` number (reused from `web/components/motion/primitives.tsx`) + a
one-line `scoreBand` verdict. **Solid arc = current `aeoScore`; ghosted arc =
`aeoScoreCeiling`** ("where completing the plan gets you").

**Decision:** in spec #1 the real number only moves on a re-audit; completion feedback
comes from the progress bar + refilling tray. The "number ticks up as you check things"
mechanic is deferred to B-spec-2 so a *rising score is always re-crawl-verified*, never
self-graded.

## Isolation, errors, testing

- **Boundaries:** `score.ts` (pure), `plan_state.py` (single-responsibility repo),
  endpoints (thin), `/plan/[id]` (presentation), `usePlanProgress`/`TodayTray`/`ScoreRing`
  (focused units) — `PhasedPlanView` doesn't bloat.
- **Errors / degradation:** plan-state create/get are reliable (proper 404, not
  swallowed); `/plan/[id]` shows a friendly "this plan link isn't available → start fresh"
  on 404; progress PUT is fire-and-forget with a localStorage mirror so a check is never
  lost; auto-resume failure = no banner, silent. API unreachable → falls back to today's
  localStorage-only behavior.
- **Tests:** `tests/unit/test_plan_state.py` — offline migration-discovery + repo-API +
  `new_id` uniqueness/charset (mirrors `test_storage_schema.py`). Web verified via
  `next build` (typecheck) + `next lint`; no JS test runner exists, so `score.ts` is kept
  defensively pure rather than adding unused harness.

## File inventory

**New (7):** `0012_plan_states.sql`, `repos/plan_state.py`, `tests/unit/test_plan_state.py`,
`web/lib/score.ts`, `web/app/plan/[id]/page.tsx`, + `ScoreRing`/`TodayTray`/`usePlanProgress`
/`ResumedPlanView` (in `results.tsx`). **Touched (5):** `src/aeo/api/app.py`,
`web/lib/api.ts`, `web/lib/types.ts`, `web/components/results.tsx`, `web/app/page.tsx`.
