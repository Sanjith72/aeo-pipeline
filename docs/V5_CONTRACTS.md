# v5 Contract Lock (CH-13)

> The locked JSON/DB contracts for the v5 build (`AEO_PRODUCT_CHANGES_v5.md`). Backend and
> frontend (`web/lib/types.ts`) must match these shapes; migrations `0027`–`0030` are the DB
> half. Change a contract here first, then both sides in the same PR.

## Resolved §9 decisions (product owner, 2026-07-23)

1. **Pack size ≤5 pages** — `MAX_PACK_PAGES = 5` in `src/aeo/pipeline/packs.py` (one-line change if it ever moves to 6).
2. **Entitlements only, payments stubbed** — real server-side unlock model; grants via manual/promo (`entitlements.source`), no payment provider yet.
3. **Re-aim: one product** — the 5-skill funnel is the single surface; Perplexity/citation machinery stays as the Discovery & Visibility engine (CH-14); blueprint-generator / coverage-diff surfaces come off the main path but the code stays.
4. **Free tier: per-IP daily limit + per-domain 24h cache** — `AEO__API__OVERVIEW_DAILY_LIMIT` (≈3 in production, 0 = off for dev), `OVERVIEW_CACHE_TTL_SEC = 86400` in `src/aeo/pipeline/overview.py`. No captcha, no email wall.

## Identity spine (P0 decision)

Three identity systems exist today: agency-era `clients.id` (milestones/shares), the anonymous
`aeo_sid` session (`plan_states`, events, gamification), and — once CH-07 lands — Supabase
`auth.users.id`. **The v5 spine is `app_users.id` (UUID = the Supabase JWT `sub` claim)**,
bridging the pre-auth session via `app_users.session_id`. Tickets and entitlements key off it;
`clients.id` becomes nullable-legacy once auth lands (P4 adds
`implementation_milestones.owner_user_id UUID`). Do not key new P4/P5 tables to `clients.id`
or bare `session_id`.

## (a) 5-skill per-page scoring output — `skills_version: "1.0"`

A **derived layer** over the existing 10-criterion rubric. `rubric_scores_v2`, the 10-key
`SCORERS` contract, and `RUBRIC_VERSION = "2.0"` are untouched; the layer is versioned
separately (`skills_version`) and recomputable from criterion tiers + evidence.

Criterion → skill map (`src/aeo/scoring/skills.py`):

| Skill | Source criteria |
|---|---|
| `messaging` | *net-new* — P1: deterministic title/meta/H1 heuristics (`confidence: "provisional"`); P2: LLM-judged `messaging_clarity` |
| `conversion` | *net-new* — P1: deterministic CTA/pricing/mid-funnel link heuristics (`confidence: "provisional"`); P2: LLM-judged `conversion_path` |
| `discovery_visibility` | `schema_markup`, `qa_blocks`, `heading_structure`, `entity_consistency` (+ CH-14 AI-visibility signal later, additive) |
| `proof_trust` | `citation_signals`, `stats_in_html` |
| `structure_ux` | `answer_readability`, `load_speed`, `render_accessibility`, `content_depth` |

Scores are 0–100 (mapped criteria: mean of 1–5 tiers rescaled; tier t → (t−1)/4·100).
`confidence` ∈ `deterministic | hybrid | provisional | neutral` — `hybrid` when any source
criterion used the LLM, `provisional` for the P1 heuristic Messaging/Conversion,
`neutral` when a skill genuinely couldn't be judged (never a fake 0). LLM-down must degrade
to `provisional`/`neutral`, never floor the score.

```json
{
  "skills_version": "1.0",
  "overall": 58,
  "skills": {
    "messaging": {
      "score": 62,
      "confidence": "provisional",
      "source_criteria": [],
      "suggestions": [
        { "id": "sug:messaging:title", "text": "Write a title tag that says what you do and who it's for.", "criterion": null }
      ],
      "evidence": { "tier_inputs": {}, "signals": { "title_ok": true } }
    },
    "conversion": { "…": "same shape" },
    "discovery_visibility": { "…": "same shape, source_criteria/tier_inputs populated" },
    "proof_trust": { "…": "same shape" },
    "structure_ux": { "…": "same shape" }
  }
}
```

Each skill carries 2–3 suggestions (fewer only when everything scores high). P2 adds optional
`predicted_lift_points` + `basis` per suggestion, reusing `validation/predict.py`'s honesty
vocabulary (`simulated | no_deterministic_lift | unknown`; unknown renders "—", never +0).

DB: `skill_scores` (migration `0027`) — one row per (page, run, skills_version); the six
score columns are the queryable summary, `detail` JSONB holds the full per-skill payload.

## (b) Pack object

Built by `src/aeo/pipeline/packs.py::build_packs` (pure) from the prioritization ranking
(`ScoredUrl`). Invariants: the homepage is **always in Pack 1** (an explicit rule — its
base_weight 0.7 does not guarantee top rank); no pack exceeds `MAX_PACK_PAGES = 5`; packs
after Pack 1 are grouped by page-type family and ordered by descending `impact_score`
(summed `final_score`).

```json
{
  "pack_index": 1,
  "title": "Homepage & first impression",
  "impact_score": 4.2,
  "page_count": 5,
  "pages": [ { "url": "https://example.com/", "page_type": "homepage", "final_score": 0.7, "rank": 4 } ],
  "locked": false,
  "status": "preview"
}
```

`status` ∈ `preview | unlocked | crawled | scored`. `locked` is derived at the API layer
(pack_index > 1 without entitlement). DB: `packs` + `page_priorities.pack_index`
(migration `0028`); the P1 free overview returns packs **unpersisted** (persistence starts
in P3 when packs become purchasable objects). `page_priorities.selected` semantics are
unchanged — `pack_index` layers on top of `rank`.

## (c) Ticket object — additive extension of `milestone_tasks`

`milestone_tasks` already carries `id, task_key, label, action_required, how_to, verify_kind,
verify_target, status, status_source, detected_run_id, detected_at, position, current_state,
prompts`. Migration `0029` adds: `assignee`, `target_date`, `page_url`, `skill`,
`baseline_score`, `current_score`, `closed_at`, and a fourth status
`closed_pending_verify`.

```json
{
  "id": 123, "task_key": "page:pricing", "label": "…",
  "status": "pending | in_progress | closed_pending_verify | verified_completed",
  "status_source": "manual | crawl",
  "assignee": "sam@acme.com", "target_date": "2026-08-01",
  "page_url": "https://example.com/pricing", "skill": "conversion",
  "baseline_score": 41, "current_score": 63,
  "detected_run_id": 45, "detected_at": "…", "closed_at": "…"
}
```

Spec's open/closed maps to: open = `pending | in_progress`; closed =
`closed_pending_verify` → (re-crawl proves the lift) → `verified_completed`.

**Close flow (P5):** setting `closed_pending_verify` pins `baseline_score` (from
`skill_scores` at ticket open), enqueues a targeted re-crawl via the existing
`enqueue_batch` **with a new `force_recrawl: true` payload flag** —
`worker._dispatch`'s crawl_batch path must pass it through to `run_urls`, otherwise an
unchanged page fingerprint-skips and never verifies. On re-score, the completion hook
writes `current_score` and flips to `verified_completed` with `status_source='crawl'`
(`mark_verified` never downgrades — preserve). Frontend: `MilestoneStatus` stays 3-state
until P5; the 4-state `TicketStatus` type is locked in `web/lib/types.ts` now
(`StatusControl`, `STATUS_META/STATUS_ORDER`, `useQuestTracker.patchTask` hardcode the
3-state enum and are updated together in P5).

## (d) Entitlements — `app_users` + `entitlements` (migration `0030`)

Enforcement is **application-level SQL** in `get_current_user`-guarded routes (the backend
connects as table owner, which bypasses non-FORCE RLS — see 0026; RLS on these tables only
closes the Supabase Data API).

```json
{
  "user_id": "8f2c…-uuid", "domain": "example.com",
  "scope": "free_overview | pack | all_packs | tickets",
  "pack_index": 2, "expires_at": null, "source": "manual | stripe | promo"
}
```

Pack 1 is free after login (implicit in the resolver — `pack_index == 1` is always
unlocked, so anonymous users with zero entitlement rows still see Pack 1; no signup row
needed); `all_packs` is the agency/advanced override from CH-02. `source` starts as
`manual`/`promo` (payments stubbed); a Stripe integration later only adds writers.

**Resolved unlock rule (P3) — the two gates combine as OR, entitlements authoritative**
(spec CH-02 states progressive unlock unconditionally, so the reconciliation is recorded
here). A pack is **unlocked** when any of: it is Pack 1; the viewer holds `all_packs`
(bypasses progression — the override exists to skip the earn-forward work); the viewer
holds a `pack` grant for that `pack_index` (you paid, you're in — regardless of
completion); or (free path) the previous pack is completed. The pure resolver is
`aeo.entitlements.logic.is_pack_locked` / `decorate_pack`; the free overview and the pack
API both route through it so their lock derivation can't drift. **"Pack completed" is
empty in P3** (`completed_pack_indices=frozenset()` — progression is inert until the P5
ticket-verified signal exists); the anonymous overview therefore shows Pack 1 unlocked and
every deeper pack locked. P4 swaps `grants=[]` for the logged-in user's real grants and
adds request-rejection; the resolver never changes.

## Migration rules (repo conventions — enforced)

- Next free number continues from `0030`. Additive + idempotent (`IF NOT EXISTS`; a
  `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` pair counts).
- Every new table self-enables RLS (`ALTER TABLE … ENABLE ROW LEVEL SECURITY`) — 0026 only
  covered tables that existed then.
- Migrations run at container boot over the app's DSN: a broken migration blocks API startup
  on every host. Rehearse on the local PG17 (port 5433) first.
- Regenerate the Supabase baseline (`scripts/export_supabase_baseline.py`) in the same PR as
  any migration.
