# AEO Pipeline — v5 Product Changes (Build Spec for Claude Code)

> **Purpose:** This is the authoritative, implementation-ready change list for the AEO pipeline moving forward. It consolidates three inputs: (1) the meeting transcript, (2) the organizer's working spec (`AEO_spec.pdf`, "AI-first Website Improvement Product", updated 2026-07-20), and (3) the UI/friction analysis of the reference site `https://prerender.io/ai-seo-tracker-template/`.
>
> **How to use with Claude Code:** Each change is a self-contained work item with an ID, current-state file references, the target behavior, concrete tasks, and acceptance criteria. Implement in the phase order in §2. Treat "Open Decisions" (§9) as inputs to confirm with the product owner before building the affected items.
>
> **Repo:** `aeo-pipeline` · Backend `src/aeo/**` (Python + FastAPI) · Frontend `web/**` (Next.js App Router + React + Tailwind) · DB Postgres/Supabase (`supabase/`, `src/aeo/storage/migrations/*`).

---

## 1. The strategic shift (context — read first)

The current codebase (`aeo_architecture_v4.md`) is a **niche Answer-Engine-Optimization pipeline**: it scores pages against a 10-criteria technical rubric (citation signals, schema markup, stats-in-HTML, answer readability) to get one cybersecurity vertical (PEV/Securin; competitors Pentera/Cymulate/Picus) cited by AI answer engines.

The target (`AEO_spec.pdf`) is a **horizontal, self-serve B2B "fix your homepage + top pages" product** for $5–30M sales-led companies, scored on **five outcome skills** (Messaging, Conversion, Discovery & Visibility, Proof & Trust, Structure & UX), monetized through **progressive packs** behind a **signup/login + payment gate**, entered through a **URL-first, low-friction PLG funnel**.

This is a **re-aiming of the existing engine**, not a rebuild. The crawl → extract → score → recommend → verify spine carries over; the rubric framing, the funnel, the gating, and the UI change. Keep every change traceable to this shift.

---

## 2. Build sequence (thinnest valuable slice first)

| Phase | Theme | Change IDs | Rationale |
|------|-------|-----------|-----------|
| **P0** | Decisions + contracts | Open Decisions (§9), CH-13 | Nothing downstream is real until pack size, pricing model, and the 5-skill schema are locked. |
| **P1** | URL-first entry + free overview (PLG) | CH-09, CH-01, CH-11a/b | Prove the low-friction funnel end-to-end with the existing engine before monetizing. |
| **P2** | Rubric reframe → 5 skills | CH-04, CH-06, CH-16 | The scoring model is the product's core; everything visual depends on it. |
| **P3** | Pack construction + progressive unlock | CH-03, CH-02b | Turn scored pages into ordered, bounded packs. |
| **P4** | Auth + gating + monetization | CH-07, CH-02a | Gate deep value; introduce payment/credits. |
| **P5** | Ticket workflow + before/after verify | CH-08, CH-15 | The "do the work + prove it improved" loop. |
| **P6** | UI redesign + friction reductions | CH-10, CH-11c–g, CH-12 | Consolidate strategy/roadmap, journey-to-top, visual rankings, Prerender friction fixes. |
| **P7** | AI-snapshot visibility metric | CH-14 | Marketable Discovery metric; reuses existing Perplexity check. |

> **Status (2026-07-23): P0 + P1 + P2 + P3 + P4 implemented.** §9 decisions resolved (see §9);
> contracts locked in `docs/V5_CONTRACTS.md`; migrations `0027`–`0031` applied + Supabase
> baseline regenerated.
>
> - **P0/P1** — `POST /api/overview` (free 5-skill homepage overview + pack preview,
>   per-domain 24h cache, per-IP + global daily caps, SSRF-guarded crawl transport),
>   `/overview` page, hero URL field (CH-11a), server-side name derivation (CH-01 — URL is
>   the only required input anywhere), `scoring/skills.py`, `pipeline/packs.py`,
>   `pipeline/overview.py`.
> - **P2 (CH-04/CH-06/CH-16)** — Messaging & Conversion are now LLM-judged on the deep audit
>   (`hybrid`) via `nlp/prompts/{messaging_clarity,conversion_path}.txt`, with the P1
>   heuristics as the deterministic-`provisional` floor (free tier stays deterministic — the
>   cost boundary). Per-skill weights + an impact-ranked `priorities` list (`config/scoring.yaml`
>   → `skills:` block; "50 from 500" by weight × severity), a **weighted** overall.
>   `skill_scores` persisted on each scored deep-audit page (`storage/repos/skill_scores.py`,
>   pipeline hook, `AEO__SCORING__SKILL_LLM` toggle). CH-16's metadata-only boundary is the
>   P1 overview (cheap homepage scan, deep crawl only on the "go deeper" action).
> - **P3 (CH-03/CH-02b)** — packs persisted on each deep-audit run (`storage/repos/packs.py`
>   `put_for_run`/`by_run`/`by_domain`; headers in `packs`, membership on
>   `page_priorities.pack_index`; `run_site` seam after `persist_ranking`, best-effort). New
>   `GET /api/packs/{run_id}` + a shared `PackCard` rendering both the overview preview and the
>   persisted list. Entitlements model (`storage/repos/entitlements.py` + the pure
>   `entitlements/logic.py` resolver): unlock = OR, entitlements authoritative — Pack 1 free,
>   `all_packs` override, `pack` grant unlocks that index, else progressive (see
>   `docs/V5_CONTRACTS.md §d`). `POST /api/entitlements/grant` (X-API-Key-gated manual/promo,
>   payments stubbed). Anonymous tier = Pack 1 unlocked, deeper locked.
> - **P4 (CH-07/CH-02a)** — Supabase-JWT user auth (`api/auth.py`, stateless HS256; the
>   `role`/`aud`/UUID-`sub` checks reject the public anon + service_role keys, which are
>   same-secret JWTs). `get_optional_user`/`get_current_user` compose with (never replace)
>   the global service `require_api_key`. **Server-side gating:** `GET /api/packs/{run_id}`
>   binds `locked` to the logged-in user's real grants; `GET /api/packs/{run_id}/{pack_index}`
>   returns the pack's page-level skill detail only if unlocked (**403 enforced server-side**,
>   Pack 1 free even anon). `POST /api/entitlements/redeem` (promo code → real `all_packs`
>   grant, source='promo'; monetization stub — payments still deferred). Migration `0031`
>   adds `implementation_milestones.owner_user_id` (unused until P5). Frontend: degradable
>   `@supabase/supabase-js` login/signup + Bearer attach + unlock UI (all hidden without env).
>   **Deploy env:** backend `AEO__AUTH__JWT_SECRET` (+ `AEO__AUTH__PROMO_CODES`), frontend
>   `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` — both sides must be
>   configured together (HS256; JWKS/asymmetric is a documented follow-up). `completed_pack_indices`
>   stays empty until P5 tickets, so login alone never unlocks — only an entitlement does.
>
> Note: the codebase reality differs from some "Current" notes below — the wizard is 4 steps
> (not 9), and the LLM router lives at `src/aeo/nlp/providers.py` (not `src/aeo/llm/`).

---

## 3. Product-direction changes (from transcript + spec)

### CH-01 — URL-first intake (remove upfront form fields)
- **Source:** spec ("URL first"; "each extra form field is a drop-off risk"); transcript ("Next Steps").
- **Current:** `web/components/StudioApp.tsx` drives a **9-step wizard** (see `web/DESIGN.md` → "9-step wizard"). Backend intake at `src/aeo/intelligence/intake.py`; competitor input via `web/components/CompetitorPicker.tsx`.
- **Target:** A single website URL is the only required input to begin. Everything else (competitors, topic, business context) is inferred from the crawl or requested *later*, optionally.
- **Tasks:**
  - Reduce required intake to `{ url }`. Move all other fields to optional, post-overview refinement.
  - Backend: `intake.py` accepts URL-only and derives context (industry, competitors) via existing `intelligence/site_facts.py`, `intelligence/industry.py`, `reference/competitor_discovery.py`.
  - Keep manual override of inferred values available but never required.
- **Acceptance:** A new user can trigger an analysis by entering only a URL; no other field blocks submission.

### CH-02 — Gating + monetization layer
Split into **CH-02a (gating/auth)** and **CH-02b (packs/payments)**; see also CH-07 (auth infra) and CH-03 (pack construction).
- **Source:** spec ("Gating and Monetization"); transcript ("Add sign-up/login gating layer").
- **Current:** No gating — anyone can crawl and get full results; no identity, no cost control, abuse possible.
- **Target:** High-level summary is free/public; **full insights unlock only after login**; **homepage/Pack 1 free, subsequent packs paid**; packs unlock progressively (must complete Pack 1 before Pack 2), with an **override for advanced users/agencies**.
- **CH-02a tasks (gate):** Public route returns overview only; full report/pack routes require an authenticated session (see CH-07). Enforce server-side, not just UI hiding.
- **CH-02b tasks (monetize):** Introduce entitlements (which packs a user has unlocked) + a payment/credit system at the login stage for Pack 1 onward. Pricing model is an **Open Decision** (flat per pack / credit-based / escalating tiers — §9).
- **Acceptance:** Unauthenticated users see only the overview; authenticated free users see Pack 1; additional packs require entitlement; entitlement checks are enforced in the API.

### CH-03 — Pack construction logic
- **Source:** transcript ("Implement pack construction logic … not alphabetical: use LLM ranking or sitemap hierarchy to group up to 6 pages per pack"); spec ("bucket of up to 6 pages" / "No pack exceeds 5 pages" — reconcile, §9).
- **Current:** `src/aeo/crawl/prioritize.py` ranks pages (top-N); `crawl/discovery.py` builds sitemap; no notion of bounded, ordered "packs."
- **Target:** Pages are grouped into **logical packs of ≤5 pages** (confirm 5 vs 6 in §9), ordered by **expected impact** (not crawl/alphabetical order). Homepage is always **Pack 1**, highest weight, the entry point for rubric evaluation.
- **Tasks:**
  - Add a pack-builder module (e.g. `src/aeo/pipeline/packs.py`) that consumes prioritized pages + sitemap hierarchy and emits ordered packs with a value score per pack.
  - Grouping signal: LLM ranking and/or sitemap hierarchy + page type (homepage, product, pricing, trust). Reuse `intelligence/site_facts.py` page classification.
  - Persist packs (new table/migration; see CH-13 schema contract).
- **Acceptance:** Given a crawled site, the system returns ordered packs, each ≤ the agreed page cap, homepage in Pack 1, packs sorted by descending expected value.

### CH-04 — Reframe rubric → 5 outcome skills
- **Source:** spec ("Skills & house rules" — 5 skills, per-page score + suggestions).
- **Current:** `src/aeo/scoring/rubric.py` + `src/aeo/scoring/scorers/*` implement 10 answer-engine criteria (`answer_readability`, `citation_signals`, `content_depth`, `entity_consistency`, `heading_structure`, `load_speed`, `qa_blocks`, `render_accessibility`, `schema_markup`, `stats_in_html`). Aggregation in `scoring/aggregator.py`, `scoring/result.py`.
- **Target:** Score each page on **five skills**, each normalized **0–100**, each with **2–3 specific suggestions**:
  1. **Messaging** — clarity & positioning (what it is / who it's for / why it matters, in plain language).
  2. **Conversion** — one primary CTA matching the stage, mid-funnel path, objection handling, logical flow.
  3. **Discovery & Visibility** — findable by humans + AI/search: titles/meta, internal links, headings, FAQ blocks, (later) schema; **includes AI-snapshot visibility (CH-14)**.
  4. **Proof & Trust** — social proof, concrete example/result, risk handling, findable pricing/commitments.
  5. **Structure & UX** — visual hierarchy, chunking, consistent patterns, expected nav/footer/search.
- **Tasks:**
  - Introduce a 5-skill scoring layer. **Reuse mapping where possible:** Discovery ← heading_structure/schema_markup/qa_blocks/meta; Proof & Trust ← citation_signals/`extract/eeat.py`; Structure & UX ← answer_readability/render_accessibility/content_depth. **Net-new (LLM-judged):** Messaging, Conversion.
  - Output contract per page: `{ skill: { score_0_100, suggestions: [ …2–3 ] } }`.
  - House rules are heuristics and may vary by segment — keep them configurable, not hard-coded.
- **Acceptance:** Every scored page returns 5 skill scores (0–100) each with 2–3 concrete suggestions; existing extractors are reused where mapped; Messaging/Conversion produce non-empty, page-specific output.

### CH-05 — "Let the AI decide, humans validate" (override capture)
- **Source:** spec core principle.
- **Current:** `web/components/AgentReviewQueue.tsx`, human-review path in `aeo_architecture_v4.md`; feedback repo `src/aeo/storage/repos/feedback.py`; events `storage/repos/events.py` (override index migration `0014_events_override_index.sql`).
- **Target:** AI makes the initial call on structure/prioritization/recommendations; users can override/refine; **overrides are captured as a learning signal**.
- **Tasks:** Ensure every AI decision surfaced in the UI is inspectable and overridable; log overrides through `events`/`feedback` for later model/rule refinement.
- **Acceptance:** A user can override any AI recommendation; the override is persisted with enough context to learn from.

### CH-06 — Impact-ranked prioritization ("50 from 500")
- **Source:** transcript ("surface the critical 50 fixes from a potential 500, prioritized by impact"; "credit score, where defaults matter most"); spec (weightages, not all elements equal).
- **Current:** `scoring/aggregator.py`, `storage/repos/priorities.py`, `recommender/*`, `report/packager.py`; predicted lift in `validation/predict.py`, `web/lib/predictedLift.ts`.
- **Target:** Assign **weightages** to rubric elements (credit-score model — high-weight failures dominate). **Surface failing high-weight items first.** Never dump unranked issues.
- **Tasks:**
  - Add per-element weights to the 5-skill rubric; compute an impact-ranked issue list per page/pack.
  - Recommendation surfacing sorts by (weight × severity × predicted lift).
- **Acceptance:** For any page/pack the UI shows a small ranked set of the highest-impact fixes first; low-weight passes are not surfaced above high-weight failures.

### CH-07 — Auth infrastructure
- **Source:** spec (gating at login; payment/credit at login stage).
- **Current:** `src/aeo/api/app.py` (FastAPI, ~65KB, no auth dependency); Supabase present (`supabase/`, RLS migration `0026_rls_hardening.sql`); share tokens exist (`migrations/0016_plan_share_tokens.sql`, `web/app/share/[token]/page.tsx`).
- **Target:** Real user accounts; session-based access control; foundation for entitlements (CH-02b) and assignees (CH-08).
- **Tasks:**
  - Add Supabase-JWT auth as a FastAPI dependency (`Depends(get_current_user)`); protect full-result/pack routes; leave the overview route public.
  - Frontend: signup/login flow; authenticated fetches in `web/lib/api.ts`.
  - Enforce RLS on user-scoped tables.
- **Acceptance:** Protected endpoints reject unauthenticated requests; a logged-in user only sees their own data; overview stays public.

### CH-08 — Ticket-system workflow (ID, status, assignee, async)
- **Source:** spec ("Workflow vision: ticket-system style tracking … each issue has an ID, status, and assignee. Async: different teams pick up packs when they have capacity"); reinforced by Prerender template columns (task status, owner, optimization dates).
- **Current:** `src/aeo/storage/repos/milestones.py`, `plan_state` (`migrations/0012_plan_states.sql`, `0015_implementation_milestones.sql`, `0024_milestone_task_context.sql`); `web/components/MilestoneDashboard.tsx`, gamified `web/components/quest/*`. **Missing: assignee + before/after score pair.**
- **Target:** Each finding is a ticket: `{ id, page_url, skill, issue, status (todo/in_progress/done), assignee, target_date, baseline_score, current_score }`. Teams pick up packs asynchronously.
- **Tasks:** Extend milestones/plan_state schema with `assignee` and `target_date`; wire before/after scores from CH-15; expose a board UI (statuses + owner + dates).
- **Acceptance:** Each issue has an ID, a settable status, an assignee, and a date; packs can be worked independently/asynchronously; state persists.

---

## 4. Reference-site changes (from Prerender.io)

### CH-14 — AI-snapshot visibility metric
- **Source:** Prerender template ("AI snapshot — whether your content is featured on Google AI Overviews, ChatGPT").
- **Current:** Perplexity citation machinery exists (`src/aeo/nlp/perplexity.py`, `validation/predict.py`, `validation/independent.py`, `validation/simulate.py`).
- **Target:** A surfaced metric: does this page appear in / get cited by AI answer engines (AI Overviews / ChatGPT / Perplexity). Lives under the **Discovery & Visibility** skill (CH-04) and powers a PLG hook.
- **Tasks:** Expose the existing citation test as a per-page "AI visibility" signal; render it in results; use it in the free benchmark headline.
- **Acceptance:** Each analyzed page reports an AI-visibility status backed by a real engine query; it appears in the Discovery skill and the free overview.

### CH-15 — Before/after (baseline → optimize → verify) surfaced
- **Source:** Prerender template ("before/after optimization comparison"); reinforces spec's re-crawl verification.
- **Current:** Content-hash gate + re-crawl exist (`aeo_architecture_v4.md`; `crawl` + `pipeline/orchestrator.py`), but the delta isn't a first-class UI object.
- **Target:** Every ticket/fix shows **baseline score vs current score**; closing a ticket triggers a re-crawl that **proves the lift**. This is the "reason to come back" loop.
- **Tasks:** Store baseline + current per ticket (CH-08); on ticket completion, trigger targeted re-crawl + re-score; render the delta.
- **Acceptance:** Users see before/after per fix; completing work triggers verification and updates the score; the delta is visible.

### CH-11 — Friction reduction & landing UX (Prerender patterns)
- **Source:** Prerender.io UX analysis (one-action value, no signup before value, preview output, concrete "what you get," 4-step how-it-works, progressive disclosure, audience framing).
- **Current:** `web/app/page.tsx`, `web/components/ui/horizon-hero-section.tsx` (WebGL starfield hero, `DESIGN.md`), 9-step wizard (`StudioApp.tsx`), gamified quest map (`components/quest/*`), abstract trust band.
- **Target & tasks:**
  - **CH-11a — Single-URL hero:** hero's primary (and only initial) action is a URL field + one CTA ("Analyze my site"). Replaces the 9-step wizard as the entry. (Depends on CH-01.)
  - **CH-11b — No signup before value:** show full value prop + a result preview before any auth ask; the free overview *is* the preview. (Ties to CH-02a.)
  - **CH-11c — Real report preview in hero:** embed a live/sample result (reuse `components/results.tsx`) so users see the deliverable before committing — not just the starfield.
  - **CH-11d — Concrete "what you get" strip:** the 5 skills with one example fix each, near the hero.
  - **CH-11e — Simple 4-step how-it-works:** Paste URL → See overview → Open homepage pack → Fix & re-verify (with thumbnails). Replaces the 9-step framing.
  - **CH-11f — Defer gamification:** don't teach the quest metaphor on first contact; reveal `components/quest/*` after the first result/pack. Keep it for retention, not onboarding.
  - **CH-11g — Audience-framed trust band:** reframe the three trust cards around who it's for (founders of $5–30M B2B, in-house marketers, agencies) rather than abstract properties. Keep the "no fabricated logos" honesty from `DESIGN.md`.
- **Acceptance:** First-time visitor can start with one field and no signup; sees a real result preview + the 5 skills + a 4-step explanation above the fold-ish; gamification does not appear before the first result; trust band names the audience.

---

## 5. UI restructure changes (from transcript + spec)

### CH-10 — Consolidate Roadmap + Strategy; journey-to-top; visual section rankings
- **Source:** spec ("Consolidate Roadmap and Strategy into one section"; "Move the journey/progress visual to the top"; "Represent homepage pack results visually with clear section-level rankings"); transcript same.
- **Current:** Separate Strategy/Roadmap surfaces: `web/components/StrategyExtras.tsx`, `report/strategy.py`, milestones; recommendations rendered low in the results; `web/lib/phases.ts`.
- **Target:**
  - **One unified section** where **strategy drives the roadmap** (not two tabs).
  - **Journey/progress visual at the top** to prompt action.
  - **Section-level rubric results shown visually** with priority ranking: what sections exist, what's missing, what's wrong per section, and the fix.
  - Recommendations surfaced by priority (CH-06), not buried at the bottom.
- **Acceptance:** Strategy and Roadmap are one section; progress visual is at the top of the pack/results view; each section shows exists/missing/wrong/fix with ranked priority.

### CH-16 — Homepage-first overview + progressive disclosure
- **Source:** spec ("Sitemap processed first using metadata only, no full page crawl yet"; "high-level site summary before any pack"; "Want to go deeper at page level?").
- **Current:** `pipeline/orchestrator.py` runs the full pipeline; `crawl/discovery.py` has sitemap discovery; no metadata-only overview stage separated from deep crawl.
- **Target:** A cheap **metadata-only overview** (sitemap + initial signals) renders a high-level summary (page groups, skill scores, weak spots) **before** any deep crawl; deep crawl + pack creation happen only when the user chooses to go deeper.
- **Tasks:** Add an overview stage that uses sitemap metadata only; gate the deep crawl behind an explicit "go deeper" action; this is also the cost boundary for gating (CH-02a).
- **Acceptance:** Entering a URL yields a fast overview without a full crawl; the deep crawl runs only on explicit user action.

---

## 6. PLG / free-benchmark change

### CH-09 — Free benchmark entry (lead magnet)
- **Source:** spec ("Free tier — paste your website, see how you stack up against competitors on messaging, clarity, conversion"; preview of one pack).
- **Current:** No free/gated tiering; competitor logic in `reference/competitor_discovery.py`, `reference/competitor_patterns.py`.
- **Target:** Free output = high-level skill scores + simple competitor comparison + preview of one pack (usually homepage) with a few concrete suggestions. Full workflow, re-crawls, deeper suggestions sit behind signup then paid tiers.
- **Tasks:** Compose the free response from CH-16 (overview) + CH-04 (skill scores) + one homepage pack preview + a lightweight competitor comparison; everything else requires auth (CH-02a/CH-07) or entitlement (CH-02b).
- **Acceptance:** An anonymous user gets scores + competitor comparison + one homepage pack preview for free; deeper value requires login/payment.

---

## 7. Cross-cutting / contracts

### CH-12 — Design-system alignment for new surfaces
- Reuse `web/DESIGN.md` tokens/components (Celestial Blueprint: Space Grotesk / IBM Plex, blueprint grid, monochrome + accent). New hero, packs, board, and results views must use existing utility classes (`.card`, `.btn`, `.blueprint-grid`) and honor accessibility rules (focus ring, `prefers-reduced-motion`, ≥40px targets).

### CH-13 — Schema/contract lock (do first)
- Lock the JSON/DB contracts before building downstream: **(a)** 5-skill scoring output (CH-04), **(b)** pack object (CH-03), **(c)** ticket object (CH-08), **(d)** entitlement/pack-access (CH-02b). Add migrations under `src/aeo/storage/migrations/` continuing the numbered sequence (latest is `0026_rls_hardening.sql`). Nothing in P2–P5 is real until these match across backend + frontend (`web/lib/types.ts`).

---

## 8. Change ↔ current-code map (quick index)

| ID | Change | Primary files to touch |
|----|--------|------------------------|
| CH-01 | URL-first intake | `intelligence/intake.py`, `web/components/StudioApp.tsx`, `web/components/CompetitorPicker.tsx` |
| CH-02a | Gating | `api/app.py`, `web/lib/api.ts` |
| CH-02b | Packs/payments | new `pipeline/packs.py`, new migration, `api/app.py` |
| CH-03 | Pack construction | `crawl/prioritize.py`, `crawl/discovery.py`, `intelligence/site_facts.py`, new `pipeline/packs.py` |
| CH-04 | 5-skill rubric | `scoring/rubric.py`, `scoring/scorers/*`, `scoring/aggregator.py`, `extract/*` |
| CH-05 | Override capture | `web/components/AgentReviewQueue.tsx`, `storage/repos/feedback.py`, `storage/repos/events.py` |
| CH-06 | Impact ranking | `scoring/aggregator.py`, `storage/repos/priorities.py`, `recommender/*`, `validation/predict.py` |
| CH-07 | Auth infra | `api/app.py`, `supabase/`, `web/lib/api.ts` |
| CH-08 | Ticket workflow | `storage/repos/milestones.py`, migrations, `web/components/MilestoneDashboard.tsx` |
| CH-09 | Free benchmark | `api/app.py`, `reference/competitor_discovery.py`, `web/app/page.tsx` |
| CH-10 | Roadmap+Strategy / journey / visual rankings | `web/components/StrategyExtras.tsx`, `report/strategy.py`, `web/lib/phases.ts`, `web/components/results.tsx` |
| CH-11 | Friction/landing UX | `web/app/page.tsx`, `web/components/ui/horizon-hero-section.tsx`, `StudioApp.tsx`, `components/quest/*` |
| CH-12 | Design alignment | `web/DESIGN.md`, `web/app/globals.css`, `web/tailwind.config.ts` |
| CH-13 | Schema lock | `web/lib/types.ts`, `storage/migrations/*` |
| CH-14 | AI-snapshot metric | `nlp/perplexity.py`, `validation/*`, `web/components/results.tsx` |
| CH-15 | Before/after verify | `pipeline/orchestrator.py`, `storage/repos/milestones.py`, `web/components/results.tsx` |
| CH-16 | Overview + progressive disclosure | `pipeline/orchestrator.py`, `crawl/discovery.py`, `web/components/StudioApp.tsx` |

---

## 9. Open decisions — RESOLVED 2026-07-23 (product owner)

1. **Pack size: 5 or 6 pages?** → **≤5 pages** (per the newer spec). Implemented as a single `MAX_PACK_PAGES` config constant so a later change to 6 is a one-line edit. Unblocks CH-03.
2. **Pricing model:** → **Entitlements only, payments stubbed.** Build the real unlock model (Pack 1 free after login, later packs locked, server-side entitlement checks, agency-override flag) but grant entitlements manually/via promo code — no payment provider yet. Pricing model (flat/credits/tiers) stays open; adding Stripe later touches only the grant path. Unblocks CH-02b.
3. **Pivot vs second surface:** → **Re-aim: one product.** The 5-skill product is the single funnel. Citation/Perplexity machinery survives as the engine behind Discovery & Visibility + AI-snapshot (CH-14); standalone AEO surfaces (blueprint generator, coverage diff) come off the main path but the code stays. Scopes CH-04/CH-14.
4. **Free-tier cost ceiling:** → **IP rate-limit + per-domain cache.** Metadata-only overview (CH-16) keeps unit cost near zero; per-IP limit (~3 analyses/day) on the anonymous endpoint; overview cached per domain ~24h. No captcha, no email wall — preserves no-signup-before-value.

---

## 10. Definition of done (v5 slice)

A first-time, unauthenticated visitor pastes a URL, gets a fast metadata overview with five skill scores + a competitor comparison + a homepage-pack preview (no signup, no wizard). Choosing to go deeper prompts signup; the homepage pack (Pack 1) is free; further packs require entitlement. Inside a pack, findings are impact-ranked tickets (id/status/assignee/date) shown in a unified strategy-driven roadmap with the progress visual at the top and section-level exists/missing/wrong/fix rankings; completing a fix triggers a re-crawl that shows the before/after score change. All new surfaces use the existing design system.
