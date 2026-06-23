# Crawl Optimization Plan (Task 3 / Approach A)

**Date:** 2026-06-17. **Status:** 2a + 2b shipped; 2c designed.

## Problem

The full audit takes ~5–15 min on the local model, and historically showed only a static
stage checklist — peak first-run abandonment.

## What already existed

Per-stage progress plumbing (`orchestrator.RUN_STAGES`, `_emit` → `jobs.record_stage` →
`Job.stages` → 2s frontend poll → `AnalysisProgress`). Content-hash caching
(`fingerprint.should_skip` clones a prior extraction/score forward). The **`dry_run`** path
(discover → blueprint → coverage → profile [→ score top N], zero-DB) is the fast seam, and
`/api/profile` already calls it on step 0.

## Slice 2a — premium loading + early findings (SHIPPED)

`AnalysisProgress` now renders, during the wait: the **fast profile we already computed on
step 0** (score, headline, journey chips) as an `EarlyFindings` card, plus a reassurance
line that cycles every 3s. Frontend-only, no backend change. Result: real value in seconds.

## Slice 2b — crawl freshness / use-existing (SHIPPED)

- `runs.latest_for_domain(domain)` — read-only JOIN `crawled_pages → crawl_runs` keyed off
  the stable url host (crawl_runs has no owner column), returns `{run_id, last_crawled_at,
  status}`. No schema change.
- `GET /api/site-freshness?domain=` → `{fresh, run_id, last_crawled_at, status, has_report}`;
  best-effort.
- Wizard fetches it leaving step 0; a banner offers "use that review" (loads the persisted
  site report — no re-crawl) or keep going to refresh. LLM-first: the system decides
  recency; the user doesn't pick crawl depth or staleness.

## Slice 2c — true incremental crawl (DESIGNED, not built)

The remaining, higher-risk optimization (touches `orchestrator` / `jobs` / `runner` +
`CrawlClient` lifecycle — warrants a review pass):

1. In the audit runner, first run `dry_run(domain, pages=1..3)` (homepage + top pages, in
   memory, seconds) and emit a **new `preview` stage** carrying `{profile, coverage,
   top_missing, page_scores}`. `Job.stages` already flows to the UI, so the frontend renders
   a real partial-result card the moment `preview` lands, then keeps polling.
2. Split `fetch_many` into **waves** (homepage wave, then remainder) and emit a `crawl`
   event per wave with running `scored/total` — turning the single terminal `crawl` emit
   into a live counter. (Confirm `CrawlClient` supports staged use without relaunching the
   browser each wave.)
3. **Partial = progressive depth, not a config form**: the preview wave *is* homepage-only;
   let the score/quality gate decide whether to auto-deepen (thin site → stop at preview;
   rich site → continue) — never a depth dropdown.

Open: keep `preview` in `job.stages` (simplest, already wired) vs a `job.result.preview`
field; ensure the preview's deterministic blueprint/coverage won't visibly contradict the
final persisted numbers (label it "preliminary" or pin the same blueprint).

## Net

The perceived-wait problem (the actual #1 pain) is addressed by 2a + 2b today. 2c makes the
*real* compute incremental and is the natural next reviewed pass.
