# AEO Redesign Checklist — Round 2 (Arun Teardown, 2026-06-17)

Second teardown. Transcript: https://notes.granola.ai/t/037c37da-4e99-40b9-96b0-d7057c9d8b15
Pairs with `CLAUDE_CODE_PLAYBOOK_R2.md`. Tags: `[Direct]` = stated by Arun, `[Likely]`/`[Guessing]` = inferred.

> **Hard rule (project instructions): one block at a time, test, integrate.** Branch per block.

---

## A. Context — R1 is already shipped; R2 is deltas

The R1 plan is largely **in the codebase already**, so do NOT re-plan it — extend it:
- `intelligence/intake.py` — `infer_industry` / `infer_location` / `classify_intake` (R1 #2/#3). ✓
- `pipeline/orchestrator.py` — `RUN_STAGES` + `_emit` progress sink (R1 #7). ✓
- `web/components/results.tsx` — tabbed dashboard (overview/blueprint/actions/kit), phased plan,
  live analysis progress, `low/medium/high` priority styling (R1 #8/#10/#13). ✓
- `storage/repos/feedback.py` + `criteria_refinements` — human-gated learning infra. ✓

So R2 = the **new** asks plus reconciling two conflicts R2 introduces.

### Conflicts to resolve BEFORE coding
1. **Priority folders vs time-phases.** R1 shipped `week_1/week_2_4/later`. Arun now wants
   `high/medium/low` folders + progressive disclosure. Pick ONE primary grouping. Recommendation:
   **priority is primary** (matches "tackle critical first / reduce overwhelm"); keep the time/phase
   as secondary metadata on each task, not a competing folder structure. [Likely]
2. **Learning loop must stay human-gated.** Arun: "capture overrides, feed back as a learning loop."
   v4 NEVER auto-applies `criteria_refinements` (circular-validation guard). Overrides are
   **captured → proposed → human-validated**, never auto-tuned. Confirm with Arun if he meant
   otherwise. [Direct + architectural guard]

---

## B. Scope split
- **Backend** (`src/aeo/`): the PEV label fix, incremental/streaming crawl, cache-age API,
  override capture, strategy clustering.
- **Frontend** (`web/`): progressive disclosure, loading animation, file-age + re-crawl control,
  Strategy tab, navigation rework.

---

## C. Blocks (dependency order)

### Block R2-0 — Manual workflow walkthrough (NO CODE)  ← Arun's exercise, gate
- [ ] Each team member manually runs the full job in Claude/ChatGPT (URL → blueprint → plan).
- [ ] Note **every** step taken, then map each step to a UI component.
- [ ] Output: a step→component map. Gaps in it are the real backlog; use it to sanity-check C below.

### Block R2-1 — Fix the industry label bug (PEV → human label)  ← quick win, do first
The taxonomy key `PEV` leaks to the UI as the industry. [Certain]
- [ ] Root cause: `config/domains/securin.io.yaml` `topic: PEV`; `intake.infer_industry()` echoes a
      specific topic verbatim.
- [ ] Fix: map topic taxonomy keys → display labels (add a `display_label`/`industry` to the topic
      in `config/framework.yaml`, or a lookup in `intake.py`), so `PEV` renders as "Cybersecurity".
      Keep the taxonomy key for measurement; only the *label* changes.
- [ ] Tests: `infer_industry` with `topic="PEV"` returns the human label, not "PEV"; generic topics
      still fall through to the business-model coarse label.

### Block R2-2 — Incremental crawl + loading UX (the 10-min wait)  ← top priority (Arun's #1 next step)
Biggest UX risk. Backend + frontend. [Direct]
- [ ] **Loading animation** — `results.tsx` already renders live `RUN_STAGES` progress (#7); extend it
      into a proper animated state, not a static spinner.
- [ ] **Homepage-first incremental** — change `orchestrator.run_site` to fetch + surface the homepage
      profile immediately, then continue the top-N crawl in the background, emitting partial results.
      (Discovery already seeds from the homepage; the gap is *surfacing* it before the full run.)
- [ ] **Cache + file-age + re-crawl choice** — `content_hash` + `crawled_at` + `fingerprint.should_skip`
      already exist. Expose last-crawl age via the API; let the UI show "data from N hours ago" and
      offer an explicit re-crawl override (bypass the skip gate).
- [ ] **Drop-off safety** — page/section-level crawling so a user abandoning mid-run doesn't burn the
      full top-N compute (early-exit / cancellation hook in the run loop).
- [ ] Tests: homepage result emitted before full-run completion; cache-age surfaced; re-crawl bypasses
      the skip gate.

### Block R2-3 — Progressive disclosure of tasks  [`web/`]  ← Arun's core UI push
Duolingo-style bite-sized, not a flat 30+ list or all buckets at once. [Direct]
- [ ] Group the action plan into **high / medium / low** folders under the progress chart (resolve
      Conflict #1: priority primary, time-phase as metadata).
- [ ] **Show only the most critical tasks by default** — collapse medium/low; reveal progressively as
      the user clears high-priority items. Reduce decision fatigue at every step.
- [ ] Lives in `results.tsx` (ActionsPanel / the phased-plan centerpiece). Pairs with R2-5/R2-6 — same
      file, so sequence them under one owner to avoid collisions.

### Block R2-4 — LLM-first: reduce questions + capture overrides  [backend + `web/`]
"If the tool asks 15 questions, users feel they could've done it themselves." [Direct]
- [ ] Audit the remaining wizard inputs; remove or auto-prefill anything the crawl can infer (builds on
      `intake.py`). The bar: the LLM decides, the human only validates/overrides.
- [ ] **Capture overrides** — when a user edits a prefilled value or rejects a recommendation, log it
      (reuse the R1 Block F `events` table if present, else add an `overrides` log).
- [ ] Route captured overrides into the existing **human-gated** `criteria_refinements` proposal flow
      (`feedback.py`) — as eval signal / proposals only. **Do NOT auto-apply** (Conflict #2).
- [ ] Tests: an override is captured; it produces a *proposed* (not accepted) refinement.

### Block R2-5 — Strategy tab (LLM clustering by difficulty/maturity)  [backend + `web/`]
- [ ] Backend: cluster tasks by difficulty/maturity grade via the LLM; persist strategy groups, each
      with a readme (`what` / `why` / `how` — reuse the R1 #12 `current_state/action/how_to` contract)
      and a linked action plan.
- [ ] Frontend: new **Strategy** tab in `results.tsx` (`TabId` union currently
      `overview|blueprint|actions|kit`) rendering the folders + readmes.
- [ ] Tests: clustering returns stable groups for a fixed input; each group has a readme + linked tasks.

### Block R2-6 — Navigation rework (tabs shouldn't jump)  [`web/`]
- [ ] Fix the section-jump jank when switching tabs/panels in `results.tsx` (preserve scroll position;
      no layout jump between `overview/blueprint/actions/kit/strategy`).
- [ ] Pairs with R2-2/R2-5 — same file; do these three under one frontend owner.

---

## D. What we are explicitly NOT doing
- Not re-planning R1 (it's shipped) — only extending it.
- Not adding a second competing folder taxonomy (priority OR time, not both as folders).
- Not auto-applying learned refinements — human-gated only (the v4 circular-validation guard).

## E. Suggested order
**R2-0 (exercise) → R2-1 (bug, quick win) → R2-2 (10-min wait, top priority) →
R2-3 + R2-5 + R2-6 (all `results.tsx`, one owner, sequenced) → R2-4 (LLM-first + override loop).**
R2-2 is the highest-impact item Arun named; ship it early. The three `results.tsx` blocks must not
run in parallel — they collide on one file.
