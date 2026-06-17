# Implementation Report — Retention Foundation + LLM-First Phase

**Date:** 2026-06-17. **Branch:** `feat/aeo-retention-foundation` (PR #3).

This branch covers two efforts: **Approach B** (the retention loop & moat, Specs #1–#2)
and the **LLM-first phase** (the 8-task meeting prompt). Built incrementally, verified at
each step (ruff · unit tests · `next build` · the dockerized integration DB · adversarial
review on the early specs).

## Completed

| Area | What shipped | Commits |
|---|---|---|
| **Spec #1 — retention foundation** | canonical AEO score · persisted/resumable plan (`/plan/<id>`, `plan_states`) · "Today" tray + score ring · priority-folder Strategy tab | b51bf9c, 4698848 |
| **Spec #2 — verified moat** | audit/plan-state hardening (SSRF guard, dedupe+cap+eviction, body caps, session_id leak, 503) · honest criterion verifier (migration 0013) · "Verified live" surfacing | 765aa63, b570c0d, f9feb26 |
| **Task 4 — industry bug** | topic-code → human industry map + a guard so no routing code (e.g. "PEV"/"PV") leaks via topic *or* category | c596bd2, 50bffad |
| **Task 7 — override eval signals** | `user_override` events + `export_overrides()` + migration 0014 index + `GET /api/eval/overrides` + `api.trackOverride` on industry/location/name | 2cee49b |
| **Task 8 — UX audit** | `docs/UX_AUDIT.md` — first-run walkthrough + ranked LLM-first fixes | a4743fb |
| **Phase 1 — friction kills** | P1 auto-build (no 2nd wait, fast by default) · P5 guarded stepper · P6 honest copy · P7 pre-selected goal | 6798d87 |
| **Phase 2 — crawl experience** | 2a premium loading + early findings during the wait · 2b crawl freshness + use-existing review | 74f6eed, 65d2d2d |
| **Tasks 1+2 — prioritization** | High/Med/Low priority folders (progressive disclosure) + deterministic per-task priority_band/impact/difficulty/rationale | 26a019d |

## Files modified (high level)

- **Backend:** `src/aeo/api/app.py` (endpoints + hardening), `src/aeo/api/jobs.py` (dedupe/cap/eviction),
  `src/aeo/storage/repos/{plan_state,outcomes,events,runs,scores}.py`, `src/aeo/validation/validator.py`,
  `src/aeo/pipeline/orchestrator.py`, `src/aeo/intelligence/{intake,config}.py`, `src/aeo/report/packager.py`,
  migrations `0012`–`0014`, `config/intelligence.yaml`.
- **Frontend:** `web/app/page.tsx`, `web/app/plan/[id]/page.tsx`, `web/components/results.tsx`,
  `web/lib/{api,types,options,score}.ts`, `web/components/chrome.tsx`.
- **Docs:** `docs/{LLM_FIRST_PRODUCT_STRATEGY,TASK_PRIORITIZATION_ARCHITECTURE,CRAWL_OPTIMIZATION_PLAN,STRATEGY_ENGINE_DESIGN,UX_AUDIT,aeo-retention-foundation-report}.md`,
  `docs/superpowers/specs/2026-06-1{6,7}-*.md`.

## Verification

- **ruff**: clean. **Unit tests**: 611 passing. **Integration** (dockerized Postgres on 5433):
  9/9 for the verifier loop + migrations idempotent (one DAU-count test is environmentally
  flaky when the live compose app shares the dev DB — not a code issue). **`next build`**:
  typechecks + lints clean. **Docker**: `aeo-web` image builds.
- Migrations `0012`–`0014` are additive/idempotent and applied to the running compose DB.

## Remaining (designed, not built) — recommended next phase

Each is large and/or LLM-heavy and warrants its own reviewed pass (a clean agent-review
quota was not reliably available during this build):

1. **Slice 2c — incremental crawl** (`CRAWL_OPTIMIZATION_PLAN.md`): `dry_run` "preview"
   stage + wave-batched `fetch_many`. Touches the crawl pipeline.
2. **Task 5 — strategy workspace** (`STRATEGY_ENGINE_DESIGN.md`): LLM clustering into
   README/metrics/checklist folders, deterministic-first, lazy-fetched.
3. **Task 6 — guided navigation**: collapse `view`+`step`+`tab` into one 6-stage
   `Journey` rail (Discover→Analyze→Prioritize→Strategize→Execute→Track); high UI churn.
4. **Task 2 LLM-refinement pass** over the priority signals (`TASK_PRIORITIZATION_ARCHITECTURE.md`).
5. **Full P2** (remove the AI toggle entirely + a "rewrite with AI" button) — Phase 1 already
   defaulted it off and reframed it as opt-in, so this is polish.

## Technical debt / notes discovered

- **Auth is not a real boundary**: `NEXT_PUBLIC_API_KEY` is baked into the browser bundle;
  it's a scanner filter, not auth. Set `AEO__API__AUTH_KEY` and/or front the API with a
  reverse proxy; add per-IP/session rate limiting. SSRF is guarded at the audit entry point
  but not per-redirect-hop.
- **Shared dev DB**: the live compose app and the integration tests write to the same
  Postgres, making whole-table metric assertions flaky locally (fine in CI).
- **Dead field**: the `challenges` textarea is collected but never sent — decide wire-or-delete.
- **`runs.py`** uses deprecated `datetime.utcnow()` (pre-existing warning).
- **No JS test runner**: the web is verified via `next build`; consider adding Vitest for
  `score.ts` / pure helpers.
