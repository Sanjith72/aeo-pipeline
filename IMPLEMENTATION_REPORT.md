# Implementation Report — AEO Redesign Round 2 (Arun's teardown)

**Date:** 2026-06-18 · **Branch:** `main` (tip `62a8978`) · **Pairs with:**
[`REDESIGN_CHECKLIST_R2.md`](REDESIGN_CHECKLIST_R2.md) · [`CLAUDE_CODE_PLAYBOOK_R2.md`](CLAUDE_CODE_PLAYBOOK_R2.md)

This report documents the work done to deliver **Arun's Round-2 teardown** (blocks R2-0 → R2-6)
plus the **retention foundation** (Approach B, Specs #1–#2) and the LLM-first follow-ups, and how
they all came together on `main`.

---

## 1. Context — two parallel tracks, reconciled

Round 2 was built on **two independent tracks** that had to be reconciled:

- **Track A (`main`, by Sanjith):** implemented Arun's full R2 checklist (R2-1 … R2-6) plus extra
  intake/competitor work, merged to `origin/main` (range `76a09e7..1653afe`).
- **Track B (retention branch):** independently re-implemented the same R2 blocks **and** uniquely
  built the **retention foundation** (Specs #1–#2) + the Task-7 eval export — which `main` did not have.

The reconciliation: rather than merge the duplicated R2 work (massive conflicts), we **kept `main`'s
R2 as authoritative** and **ported only the non-duplicated retention work** on top of it, then closed
the remaining gaps. Net result: `main` now carries **both** the full R2 redesign and the retention loop.

---

## 2. Arun's R2 blocks — status on `main`

| Block | Arun's ask | Status on `main` | Source |
|---|---|---|---|
| **R2-0** | Manual walkthrough exercise (no code) | Team exercise — informed the backlog | — |
| **R2-1** | Fix the industry label bug (PEV → human label) | ✅ topic-key → display label; per-site, no seed leak | `7c220f5`, `2f50d40` |
| **R2-2** | Incremental crawl + loading UX (the ~10-min wait) | ✅ homepage-first `profile` partial stage · cancel (`/api/audit/{id}/cancel`, drop-off safety) · cache-age on `/api/profile` · `force_recrawl` | `d617fa7` |
| **R2-3** | Progressive disclosure of tasks | ✅ high/med/low priority folders, reveal-as-you-clear | `c394e8a` |
| **R2-4** | LLM-first: fewer questions + capture overrides | ✅ override capture → human-gated proposals (`/api/overrides`) — never auto-applied | `9282d03` |
| **R2-5** | Strategy tab (cluster by difficulty/maturity) | ✅ clustered groups + readmes, Strategy tab | `5c9fa1b` |
| **R2-6** | Navigation rework (tabs shouldn't jump) | ✅ sticky tabs + reserved panel height, no scroll jump | `a8e019b` |

**Conflicts Arun flagged, as resolved:** priority is the **primary** grouping axis (time/phase is
secondary metadata); the learning loop stays **human-gated** (overrides are captured → *proposed* →
human-validated, never auto-tuned — the v4 circular-validation guard).

**Beyond the checklist (also on `main`):** crawl-derived location/services/competitors, competitor
context fed to the LLM, Wikidata industry vertical + TTL cache, competitor empty-state relaxation,
broadened site-facts extraction.

---

## 3. Retention foundation + follow-ups — the changes/fixes in this effort

Three commits ported the retention work onto `main` and closed every gap. Each was verified before push.

### `10fbe6d` — port the retention foundation (Specs #1–#2 + Task 7)

**Spec #1 — retention loop (resumable plan):**
- Migration `0012_plan_states`; `storage/repos/plan_state.py`; endpoints `POST/GET/PUT /api/plan-state`
  and `GET /api/plan-state/{id}` (allowlisted public view — no `session_id` leak; 503 vs 404 on DB hiccup).
- `web/app/plan/[id]/page.tsx` (shareable resumable page) · `ScoreRing` (canonical AEO gauge, `web/lib/score.ts`)
  · `ResumedPlanView`.
- **Merged into main's R2-3 `PhasedPlanView` additively** — added optional server-backed persistence
  (`planStateId`/`serverBacked`/`initialDone`/`score`) so progress survives a device switch; main's
  existing localStorage callers are untouched.
- Wizard wiring: `createPlanState` mints the shareable `/plan/<id>` link · resume banner on return.

**Spec #2 — verified re-crawl moat ("Verified live"):**
- Migration `0013_outcome_baseline_tier`; **criterion-honest** `outcomes.decide_status`/`mark_from_recrawl`
  (an outcome flips to *implemented* only when the targeted criterion's re-scored **tier rises**, not on a
  bare content-hash change) wired into the orchestrator **after** re-scoring.
- `GET /api/recheck-status` + the **Verified-live** card in the results overview.

**Task 7 — override eval export:** migration `0014` (override index), `events.export_overrides()`,
`GET /api/eval/overrides`, and `api.trackOverride` feeding the events stream (coexists with R2-4's
`/api/overrides`).

### `2dd26f8` — fix: criterion-honest assertions in the DB integration smoke

The port made `mark_from_recrawl` criterion-honest (4-arg), but `test_db_smoke` still asserted the old
hash-based flip — so it failed on `main`. Updated the integration smoke to the criterion-honest contract
(mirrors the already-ported unit `test_outcomes`). **This was a regression the port introduced and we caught + fixed.**

### `62a8978` — audit-endpoint hardening + Spec #1 "Today" tray

- **Audit hardening** (the deferred Spec #2 piece): request **dedupe** (collapse in-flight audits for one
  domain), **concurrency cap** (`429` over `_MAX_CONCURRENT_AUDITS`), bounded **eviction** (`_MAX_JOBS`;
  cancelled jobs evictable), and the **SSRF guard** (`_assert_crawlable_host` rejects internal/loopback) —
  all merged into main's R2-2 cancel registry. +6 tests.
- **Spec #1 "Today" tray:** the 1–3 highest-leverage next tasks (quick-wins first, then phase, then
  priority), above the R2-3 folders — a focused "do this now" surface that shrinks as items are checked off.

---

## 4. Verification

Run against the live dev DB (`localhost:5432`), not just mocks:

- **ruff:** clean. **Tests:** **687 unit + integration pass** (incl. ported `test_outcomes`/`test_plan_state`,
  4 retention endpoint wiring tests, 6 audit-hardening tests, criterion-honest integration smoke).
- **Migrations `0012`–`0014`** apply cleanly via `aeo migrate` (they slot after main's `0011`).
- **Live `plan_state` round-trip** (create → get → update → resume) verified against real Postgres.
- **`next build`** typechecks + lints clean (`/plan/[id]` route present).
- **Feature inventory:** all R2-1…R2-6 + Spec #1/#2 + Task 7 markers confirmed present in source.

---

## 5. Deliberately deferred — backlog, not missing implementations

Three originally "designed, not built" items remain **out** of `main`, each for a concrete reason
(forcing them would regress or override working code, not complete it):

1. **Task 2 — LLM-refinement over priority signals:** *superseded.* `main` derives priority bands in the
   **frontend** from `t.priority`; the deterministic backend signals the refinement was meant to enhance
   aren't on `main`. Adding them would mean replacing main's working R2-3 substrate with a parallel mechanism.
2. **Full P2 — remove the AI toggle + "rewrite with AI" button:** a **product/UX decision.** `main`
   deliberately ships the default-on "Personalize with AI" toggle; removal is a call to make, not a fix.
3. **Task 6 — 6-stage Journey rail:** a **large speculative nav rewrite** ("high UI churn") never built by
   anyone; warrants its own brainstorm → spec → build → review rather than a tail-end bolt-on.

---

## 6. Operational notes

- **Migrations:** any existing dev/prod DB must run `aeo migrate` to apply `0012`–`0014` (additive,
  idempotent). Nudge anyone sharing a DB.
- **Auth is not a real boundary:** `NEXT_PUBLIC_API_KEY` is in the browser bundle (a scanner filter, not
  auth). Set `AEO__API__AUTH_KEY` and/or front the API with a reverse proxy; the SSRF guard is at the audit
  entry point, not per-redirect-hop.
- **Shared dev DB:** the live compose app and the integration tests write to the same Postgres — whole-table
  metric assertions can be locally flaky (fine in CI).
