# AEO Verified Re-crawl Moat — Design (Spec #2 of Approach B / "B3")

**Status:** Slices A–C landed (hardening · honest verifier · "Verified live" UI).
Per-task badges + a provisional/permanent score split are deferred (see Slice C).
**Date:** 2026-06-17. **Branch:** `feat/aeo-retention-foundation`.

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

## Slice B — the honest criterion verifier (LANDED, trust-critical)

`implemented` now means "the specific fix landed", not "the page changed".

- **Baseline tier pinned at issue.** Migration `0013` adds `baseline_tier INTEGER`;
  `validator._open_outcomes` writes the targeted criterion's score tier from the page's
  baseline `PageScore` at issue time (alongside `baseline_hash`).
- **Verify after re-score, not on hash.** `_detect_completions` moved to run *after*
  `score.run()` in `_process_one`, and is handed the fresh `PageScore`. `decide_status`
  now returns `(status, method)` and marks `implemented` only when the targeted
  criterion's re-scored tier is **strictly higher** than `baseline_tier`
  (`regressed` when lower). A hash change with no tier gain — or any change we can't tie
  to a criterion improvement — stays **pending**, never falsely "verified". The
  `recommendation_implementation_rate` metric is therefore honest now.
- **Why no detection on skipped pages:** a changed page never fingerprint-skips (its hash
  differs from the prior run), so it always re-scores and is checked; an unchanged page
  can't have a pending outcome that should flip. (Documented in `_process_one`.)
- **Tests:** `test_outcomes.py` covers the new `decide_status` matrix offline; the
  integration loop in `test_db_smoke.py` now asserts changed-but-no-tier-gain stays
  pending and only a tier rise flips to `implemented` via `criterion_improved`.
- *Deferred refinement:* a re-scorable-but-flaky fallback to `not_detected` (vs. staying
  pending) — current behavior keeps it pending so future re-crawls can still confirm.

## Slice C — surface verified state (LANDED)

- **Endpoint:** `GET /api/recheck-status?domain=` → `{verified: [{url, criterion,
  detected_at}], count}`, backed by `outcomes.implemented_for_domain()` (host-matched,
  `status='implemented'` only). Best-effort: any failure returns an empty set so the
  results view never breaks over it.
- **Frontend:** a **"N fixes verified live"** card at the top of the Strategy tab —
  "verified by us, not self-reported. This is what a chatbot can't do." Fetched on mount
  and re-fetched whenever a re-check finishes (`rechecking` flips false), with criteria
  shown in plain language (`CRITERION_LABEL`). This is the moat made visible.
- **Scope adaptations (deliberate):**
  - *Domain-level summary, not per-task badges.* `PlanTask` ids are `page:{slug}` / `vis:*`
    and carry no URL or criterion, while outcomes are keyed by full `url_normalized` +
    criterion — so a faithful per-task badge needs the packager to emit url+criterion on
    each task. Deferred to a follow-up rather than faking the mapping.
  - *No score split needed.* The Spec #1 ring already moves **only** on a re-audit (it's a
    pure function of the refreshed `SiteProfile`), so there are no self-checked "provisional
    points" to separate — the score is already re-crawl-honest.

## Dependencies & order

`Slice A (done)` → `Slice B (honest verifier + migration 0013)` → `Slice C (API + UI +
score split)`. Slice C must not ship a "Verified" label before Slice B exists — that's the
non-negotiable trust gate carried over from the Spec #1 report.

## Tests

- `JobRegistry` dedupe/cap/eviction: offline unit tests (landed, `test_jobs.py`).
- Verifier decision: offline unit tests over (baseline_tier, new_tier, hash) cases.
- `0013` migration: offline discovery/columns test mirroring `test_storage_schema.py`.
- SSRF guard: unit test over public vs private/loopback hosts (mock `getaddrinfo`).
