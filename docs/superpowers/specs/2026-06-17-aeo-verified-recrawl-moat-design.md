# AEO Verified Re-crawl Moat — Design (Spec #2 of Approach B / "B3")

**Status:** in progress — security hardening landed; honest-verifier core next. **Date:** 2026-06-17.
**Branch:** `feat/aeo-retention-foundation`.

This is the moat: a re-crawl that **proves a recommended fix actually shipped on the live
site** — the one thing every persona in the teardown said they'd pay for, and the
structural answer to "why not just use ChatGPT?" (a chatbot can't watch your live site and
confirm a change landed).

## What already exists (corrected from the original assumptions)

Reading the code changed the plan. The backend retention loop is **already wired end to
end** — it is not missing, it is *invisible and hash-only*:

1. **Baselines are opened.** `validation/validator.py::_open_outcomes` calls
   `outcomes.open()` for every issued recommendation (when `retention.enabled`), pinning
   the page's `url_normalized`, the targeted `criterion`, the issuing `run_id`, and the
   `content_hash` at issue time.
2. **Re-crawls detect.** `orchestrator._detect_completions()` runs
   `outcomes.mark_from_recrawl()` for **every** crawled page on **every** audit — so the
   "Re-check my site" button (Spec #1) already triggers detection.
3. **The metric reads it.** `events.recommendation_implementation_rate()` reports the
   fraction of outcomes marked `implemented`.

So the real gaps are narrower and sharper than the synthesis assumed:

- **G1 — detection is hash-only.** `outcomes.decide_status()` flips to `implemented` on
  *any* content-hash change. Its own docstring flags this as the self-grading hazard and
  leaves criterion verification as a TODO. **We must not surface "Verified" until this is
  honest.**
- **G2 — nothing reaches the UI.** No endpoint exposes outcome status; no "Verified live"
  badge exists; the score has no provisional-vs-permanent split.
- **G3 — the audit endpoint was unhardened** (see below — landed this turn).

## Slice A — security hardening (LANDED this turn)

The re-run adversarial review surfaced a **blocker** + majors on the audit/plan-state
surface (amplified by Spec #1's "Re-check my site"). Fixed:

- **SSRF guard** (`app._assert_crawlable_host`): `POST /api/audit` resolves the target host
  and rejects private/loopback/link-local/reserved IPs (e.g. `169.254.169.254`) before
  spawning a crawl. *Remaining:* per-redirect-hop revalidation in the crawler.
- **Audit dedupe + concurrency cap + eviction** (`JobRegistry.active_for/active_count/
  _evict` + `start_audit`): an in-flight audit for the same domain returns the existing job;
  a global cap (`_MAX_CONCURRENT_AUDITS=4`) returns 429; the registry is bounded
  (`_MAX_JOBS`) so finished jobs can't leak for the process lifetime.
- **Plan-state body caps**: `_limit_body_size` middleware rejects >2 MB by Content-Length;
  Pydantic validators bound `done_task_ids` length/element size and reject >1 MB
  serialized `plan`/`profile`.
- **session_id no longer leaks**: `GET /api/plan-state/{id}` returns an explicit allowlist
  (no `session_id`); the TS type drops it too.
- **Honest read errors**: a transient DB outage on `GET /api/plan-state/{id}` is a 503 →
  the `/plan/[id]` route shows a retryable "temporarily unavailable", not "expired".

*Flagged, not fixed (deployment/architecture):* `NEXT_PUBLIC_API_KEY` is baked into the
browser bundle, so X-API-Key is a scanner filter, not an auth boundary — real per-session/
per-IP rate limiting and the auth model are a separate hardening track; and the compose
deployment should set `AEO__API__AUTH_KEY` / front the API with a reverse proxy.

## Slice B — the honest criterion verifier (NEXT, trust-critical)

Make `implemented` mean "the specific fix landed", not "the page changed".

- **Pin the baseline tier at issue.** Extend `outcomes` (migration `0013`) with
  `baseline_tier INT NULL`; `_open_outcomes` writes the criterion's score tier from the
  page's `PageScore` at issue time (alongside the existing `baseline_hash`).
- **Verify after re-score, not on hash.** Detection currently runs *before* the page is
  re-scored (`_process_one` calls `_detect_completions` first). Move criterion
  verification to *after* `score.run()` so the new tier is available, then mark
  `implemented` only when the targeted criterion's tier **rose** vs `baseline_tier`
  (re-using `rubric_scores_v2` tiers). A hash change with no tier gain is recorded as
  `changed`/still-pending, never "verified". Keep the hash short-circuit for "no change at
  all".
- **Honest fallback.** When a criterion can't be re-scored (e.g. JS-rendered, fetch
  flaked), record `not_detected` and surface "couldn't confirm yet — re-check later",
  never a false negative that implies the user didn't do the work.
- Pure decision (`decide_status` successor) stays unit-testable offline.

## Slice C — surface verified state (NEXT)

- **Endpoint:** `GET /api/recheck-status?domain=` (or by `run_id`) → per-URL/criterion
  outcome status (`implemented` / `pending` / `not_detected`).
- **Frontend:** a "Verified live ✓" badge on plan tasks the re-crawl confirmed — a state
  the user **cannot** self-toggle (distinct from the manual checkbox). Map plan tasks →
  outcomes by `url_normalized` (+ criterion where the task carries one).
- **Score split:** self-checks contribute *provisional* points; only re-crawl-verified
  outcomes bank *permanent* points and move the headline/score-ring number — so a rising
  score is always earned. (This is the B-spec-2 upgrade the Spec #1 ring deliberately left
  out.)

## Dependencies & order

`Slice A (done)` → `Slice B (honest verifier + migration 0013)` → `Slice C (API + UI +
score split)`. Slice C must not ship a "Verified" label before Slice B exists — that's the
non-negotiable trust gate carried over from the Spec #1 report.

## Tests

- `JobRegistry` dedupe/cap/eviction: offline unit tests (landed, `test_jobs.py`).
- Verifier decision: offline unit tests over (baseline_tier, new_tier, hash) cases.
- `0013` migration: offline discovery/columns test mirroring `test_storage_schema.py`.
- SSRF guard: unit test over public vs private/loopback hosts (mock `getaddrinfo`).
