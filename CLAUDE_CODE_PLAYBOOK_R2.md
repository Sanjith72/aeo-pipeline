# Claude Code Playbook — Round 2 (Arun Teardown, 2026-06-17)

One prompt per block. Run in order from the repo root. Each is self-contained: what to read, what
to change, how to test, how to commit. **Do not paste them all at once** — finish, review, merge,
then start the next. Pairs with `REDESIGN_CHECKLIST_R2.md`.

> R1 is already in the codebase (`intake.py`, orchestrator `RUN_STAGES`, `results.tsx` tabs/phased
> plan, `criteria_refinements`). These prompts EXTEND that — they do not rebuild it.
> Backend lives in `src/aeo/`, frontend in `web/`. All one repo.

---

## BLOCK R2-0 — Manual workflow walkthrough (NOT a Claude Code prompt)

Run this yourselves before coding. Each team member manually performs the full job in
Claude/ChatGPT (URL → blueprint → plan), writes down every step, and maps each step to a UI
component. Output: a step→component map. Use it to validate Block R2-3 before building.

---

## PROMPT 1 — Block R2-1: Fix the industry label bug (PEV → human label)

```
Branch `fix/industry-label`. The UI shows the topic taxonomy key "PEV" as the industry instead of a
human label like "Cybersecurity".

READ first:
- src/aeo/intelligence/intake.py  (infer_industry — note it echoes a specific `topic` verbatim)
- config/domains/securin.io.yaml  (topic: PEV)
- config/framework.yaml           (the topic taxonomy)

Root cause: infer_industry returns the raw topic ("PEV") because it's not in _GENERIC_TOPICS, so the
taxonomy KEY leaks to the UI as the industry LABEL.

Fix (keep the taxonomy key for measurement; only change the displayed label):
1. Add a human display label for each topic in config/framework.yaml (e.g. a `display_label` /
   `industry` field on the topic), and a small lookup in intake.py that resolves a topic key to its
   display label before returning it from infer_industry. Fall back to the existing behavior when no
   label is defined.
2. Set Securin's PEV topic display label to "Cybersecurity" (or the agreed human industry name).

Tests: infer_industry with topic="PEV" returns the human label, not "PEV"; an unknown/generic topic
still falls through to the business-model coarse label; existing tests stay green.

Run pytest, show the diff, commit on fix/industry-label. Do not merge.
```

---

## PROMPT 2 — Block R2-2: Incremental crawl + loading UX (the 10-minute wait)

```
Branch `feat/incremental-crawl`. The ~10-minute local-model crawl is a major UX risk. Surface
something immediately and let users avoid waiting. Backend + frontend.

READ first:
- src/aeo/pipeline/orchestrator.py  (run_site: discover -> prioritize -> crawl top-N; RUN_STAGES; _emit)
- src/aeo/crawl/discovery.py        (seeds from the homepage already)
- src/aeo/crawl/fingerprint.py and src/aeo/storage/repos/pages.py  (content_hash, last_hash, crawled_at)
- src/aeo/api/app.py and src/aeo/api/jobs.py
- web/components/results.tsx        (live analysis progress, #7) and web/lib/api.ts

Implement:
1. Homepage-first incremental: in run_site, fetch + profile the homepage FIRST and emit a partial
   result (so the UI can render the homepage findings) before continuing the top-N crawl in the
   background. Keep the existing stages; add an early partial emission.
2. Cache + file-age + re-crawl choice: expose the last crawl's age (from crawled_at) via the API.
   In the UI, show "data from N hours ago" and add an explicit "re-crawl" control that bypasses the
   fingerprint skip gate for that run.
3. Drop-off safety: add an early-exit / cancellation hook in the per-page loop so abandoning mid-run
   does not burn the full top-N compute. Page/section-level granularity.
4. Frontend: turn the live progress into a real loading animation, and render the homepage partial as
   soon as it arrives.

Tests: a homepage partial is emitted before the full run completes; cache-age is returned by the API;
the re-crawl path bypasses should_skip; cancellation stops further page work.

Run pytest and `cd web && npm run build`. Show the diff, commit on feat/incremental-crawl. Do not merge.
```

---

## PROMPT 3 — Block R2-3: Progressive disclosure of tasks (frontend)

```
Branch `feat/progressive-disclosure`. Replace the flat/all-at-once task view with Duolingo-style
bite-sized disclosure. Frontend only (web/).

READ first:
- web/components/results.tsx  (the actions/phased-plan centerpiece; PRIORITY styling low/medium/high
  already exists)
- web/lib/types.ts and web/lib/options.ts

Implement (resolve the R1-vs-R2 grouping conflict — PRIORITY is the primary axis):
1. Group the action plan into high / medium / low priority folders under the progress chart. Keep the
   existing time/phase value as secondary metadata on each task, NOT as a competing folder.
2. Progressive disclosure: by default show ONLY the highest-priority tasks; collapse medium/low and
   reveal them progressively as the user completes high-priority items. Minimize decision fatigue.
3. Do not change the API contract — consume the existing plan data.

Verify: `cd web && npm run dev`, confirm only critical tasks show first and lower buckets reveal on
progress; `npm run build` passes. Show the diff, commit on feat/progressive-disclosure. Do not merge.

NOTE: R2-3, R2-5, R2-6 all edit results.tsx. Do them sequentially on stacked branches, not in
parallel, to avoid merge collisions.
```

---

## PROMPT 4 — Block R2-5: Strategy tab (LLM clustering) (backend + frontend)

```
Branch `feat/strategy-tab` (stack on feat/progressive-disclosure). Group tasks by difficulty/maturity
grade via LLM clustering and present them as a Strategy tab.

READ first:
- src/aeo/nlp/llm.py                 (the LLM client pattern)
- src/aeo/processor/ and src/aeo/recommender/  (where tasks/recs are produced)
- src/aeo/report/builder.py          (the current_state/action/how_to contract from R1 #12)
- web/components/results.tsx         (TabId union: overview|blueprint|actions|kit) and web/lib/types.ts

Implement:
1. Backend: cluster the plan's tasks by difficulty/maturity grade using the LLM client. Persist
   strategy groups; each group carries a readme (what / why / how — reuse the existing
   current_state/action/how_to fields) and links its action items. Expose via the API.
2. Frontend: add a "strategy" entry to the TabId union and a panel in results.tsx that renders the
   folders + readmes + linked tasks.

Tests: clustering returns stable groups for a fixed input set; each group has a readme and linked
tasks; the API returns them; the tab renders.

Run pytest and `cd web && npm run build`. Show the diff, commit on feat/strategy-tab. Do not merge.
```

---

## PROMPT 5 — Block R2-6: Navigation rework (frontend)

```
Branch `feat/nav-rework` (stack on feat/strategy-tab). Tabs currently jump between sections; fix the
navigation so switching panels does not lose scroll position or cause a layout jump.

READ first:
- web/components/results.tsx  (role="tablist"/tab/tabpanel; the key={tab} panel swap)

Implement: preserve scroll position across tab switches, prevent the content jump between
overview/blueprint/actions/kit/strategy, and make navigation feel continuous (no scroll-to-top on
switch). Frontend only; do not change data contracts.

Verify: `cd web && npm run dev`, switch tabs at various scroll depths — no jump/jank; `npm run build`
passes. Show the diff, commit on feat/nav-rework. Do not merge.
```

---

## PROMPT 6 — Block R2-4: LLM-first + capture overrides (backend + frontend)

```
Branch `feat/llm-first-overrides`. Push further toward LLM-decides / human-validates, and capture
override actions as an eval/learning signal. IMPORTANT GUARDRAIL: never auto-apply learned changes —
the v4 design keeps criteria_refinements human-gated to avoid circular validation.

READ first:
- src/aeo/intelligence/intake.py     (what is already auto-inferred)
- src/aeo/storage/repos/feedback.py and the criteria_refinements table (migration 0009)
- src/aeo/reference/feedback.py      (propose_criteria_refinements, if present)
- web/app/page.tsx                   (remaining wizard inputs) and web/lib/api.ts
- (if R1 Block F shipped) the events table + track()

Implement:
1. Audit remaining wizard questions; remove or auto-prefill anything inferable from the crawl. The
   bar: the LLM decides, the user only validates/overrides. List any field you cannot safely infer
   and ask me before removing it.
2. Capture overrides: when a user edits a prefilled value or rejects a recommendation, log the
   override (reuse the events table if it exists; otherwise add an overrides log table + repo).
3. Route captured overrides into the EXISTING human-gated criteria_refinements proposal flow as
   PROPOSED items only. Do NOT auto-accept or auto-tune anything.

Tests: an override is captured; it yields a refinement with status 'proposed' (never 'accepted');
no code path auto-applies a refinement.

Run pytest and `cd web && npm run build`. Show the diff, commit on feat/llm-first-overrides. Do not merge.
```

---

## Notes
- Order: **R2-0 (exercise) → R2-1 (bug) → R2-2 (10-min wait) → R2-3 → R2-5 → R2-6 → R2-4.**
- R2-3 / R2-5 / R2-6 all edit `web/components/results.tsx` — run them on **stacked branches**, one at
  a time, never in parallel.
- Every prompt says "do not merge" — review the diff and run the suite first (one-block-at-a-time rule).
- Two decisions need Arun before/while coding: priority-vs-time grouping (R2-3) and confirming the
  learning loop stays human-gated (R2-4).
```
