# AEO Studio — UX Redesign R3 (first-time-user pass)

A first-time-user audit of the guided flow (no prior SEO/AEO knowledge) and the changes made
to remove friction. Scope: `web/` (Next.js 15 + React 19 + framer-motion + Tailwind) plus the
one backend change needed to fix the critical "Build my plan" bug.

Verified: `tsc --noEmit` clean · `next build` clean (lint + types) · `/api/deliverables`
behavior verified via FastAPI TestClient (instant plan, working async job, non-blocking start).

---

## New-user walkthrough — friction found

| # | Where | What a first-timer feels |
|---|-------|--------------------------|
| 2 | Step 0 → "Next" | Click Next, near-silent spinner on a button. "Did anything happen? Is it stuck?" |
| 3 | Goals (step 3) | Asked to pick goals from scratch, even though the analysis already knows the gaps. "Don't you already know what I need?" |
| 4 | "Big moves" / "Strategy" | A flat wall of equally-weighted recommendations. "What do I do *first*?" |
| 5 | Plan progress | A bar that only shows where you are. "Is finishing this task even worth it?" |
| 6 | "Your plan" tab | A small empty box stranded in a tall, blank panel. "This looks broken/unfinished." |
| 7 | "Build my plan" | Spinner forever, then nothing. **Critical — the core CTA returned no output.** |

---

## 7 — "Build my plan" returned nothing  ·  ROOT-CAUSED + FIXED

**Root cause.** `POST /api/deliverables` was a *synchronous* endpoint that ran full
LLM page-drafting inline (`build_asset_bundle(llm=…, draft_limit=10)`). On the Docker default
(local `qwen2.5:3b`) each page draft takes ~10 s — 10 drafts + framework + strategy = several
minutes. The Next proxy's fetch is killed by undici's ~5-min `headersTimeout`, and the proxy
then *retried the POST 3×* — duplicating the multi-minute build and starving Ollama. The deep
audit was deliberately moved to a background worker "so it never blocks the event loop";
deliverables never got that treatment. (Captured live in the logs during verification:
`page_draft_generated generator=qwen2.5:3b` at ~10 s intervals.)

**Key insight.** The *interactive* plan (`plan_for`) takes **no LLM** — it is fully
deterministic. Only the *downloadable page drafts* are slow. They were bundled into one
response, so the instant plan waited on the slow kit.

**Fix — split fast plan / slow kit (chosen approach):**
- **In-app "Build my plan" is now instant + deterministic.** The frontend calls
  `/api/deliverables` with `use_llm:false` (+ a 90 s safety timeout). The plan content is
  byte-for-byte identical; it just skips LLM page-drafting. *Verified: returns a 9-task,
  2-phase, 3-strategy-group plan in 0.06 s.*
- **AI personalization of the downloadable files is now an explicit, async background job.**
  `POST /api/deliverables/personalize` returns a job id immediately; the client polls
  `GET /api/deliverables/{id}`. Mirrors the existing audit-job pattern (`spawn_deliverables` /
  `execute_deliverables` in `api/jobs.py`). It never holds a request open, shows honest
  progress, and on failure the ready-made deterministic files remain.
- **Proxy hardened** (`app/api/[...path]/route.ts`): a POST that fails *after a long wait* is
  no longer retried (kills the duplicate-build storm); only fast keep-alive resets retry.
- **All feedback states covered:** loading (honest determinate bar), error + "Try again",
  empty-plan fallback ("rebuild" + files still available), dedupe on double-submit.

Files: `src/aeo/api/app.py`, `src/aeo/api/jobs.py`, `web/lib/{api,types}.ts`,
`web/app/page.tsx`, `web/components/results.tsx`, `web/app/api/[...path]/route.ts`.

---

## 2 — Analysis transition  ·  `web/app/page.tsx` (`AnalysisSequence`)

Leaving step 0 now replaces the wizard with a centered, animated analysis experience
(blueprint grid, pulsing "Analyzing" marker, determinate progress bar) that steps through
seven stages — *Checking structure → Discovering pages → Evaluating AEO readiness → Analyzing
content → Identifying authority signals → Spotting opportunities → Building roadmap*. The real
crawl is one call; the staging makes the wait read as the AI actively working (Perplexity/
Linear register) instead of a silent spinner. Unmounts the instant the crawl resolves so the
motion flows straight into the step-1 provisional score. Honors `prefers-reduced-motion`.

## 3 — Goals  ·  `web/app/page.tsx`

The analysis now **pre-selects** the goals it recommends, each badged **"Recommended by AI"**
(`recommendedGoals()` maps journey gaps / business model / locality / action count to outcome
goals). A banner explains "we pre-selected from your analysis — keep, untick, or add your own."
A **+ Add custom goal** input (with Enter-to-add and removable chips) lets the user type any
objective ("Rank for niche topics", etc.).

## 4 — Roadmap (was "Big moves")  ·  `web/components/results.tsx` (`RoadmapPanel`)

The flat, equal-weight list became a **phased roadmap**: **Quick wins → Foundation → Growth →
Scale**. Each phase is a collapsible card (first open) with an **objective**, **expected
impact**, and **effort** summary; moves carry a single **running order number** across the whole
roadmap (so "what's next" is unambiguous), plus category + effort pills and the "why" detail.
Progressive disclosure cuts the overwhelm — the user sees the shape first, drills in on demand.

## 5 — Progress ring  ·  `web/components/results.tsx` (`PlanProgressRing`)

The plan header now shows a **completion ring**. Hovering (or focusing) any task previews the
payoff: a ghosted arc + readout *"Finish this one: 32% → 41% complete."* Honest — every task is
an equal share of the plan, so the preview is the true next step (no invented per-task weights).

## 6 — "Your plan" dead space  ·  `web/components/results.tsx`

Root cause: the results panel reserved a min-height equal to the *tallest* tab ever rendered,
so short tabs (notably the empty "Your plan") were stranded in a tall blank box. Removed the
floor (the sticky tab bar keeps orientation), and enriched the empty state with what the plan
includes + an honest "ready in seconds" expectation. No dead space remains.

## 8 — General polish

- The "Personalize the wording with AI (~10 min)" toggle was misleading (it gated the whole
  plan). Reworded to "Write my downloadable files with AI" — clearly optional, on-demand, and
  decoupled from the now-instant plan.
- Accessibility carried through new UI: `role="progressbar"` on the ring + analysis bar,
  `aria-expanded` on roadmap/phase toggles, keyboard focus drives the hover preview, all new
  motion honors `prefers-reduced-motion`.
