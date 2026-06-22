# AEO Studio → the Operating System for AEO
## Architecture & Product Strategy: Agentized Workflow + Gamified Companion

**Date:** 2026-06-22 · **Status:** Design proposal (Phase 1–3, no code) · **Author:** Principal-architect review, grounded in a full 8-subsystem codebase map
**Decision inputs (locked with the team):** Assistive copilot + human approval gates · Hybrid LLM (frontier for reasoning, cheap/local for extraction) · Extend the existing async worker + Postgres (no new execution engine) · Agentization engine first, gamified companion after.

> **Method note.** This document is grounded in a parallel agentic analysis of the actual repository (8 subsystem mappers → architecture/gamification/cost/impact design probes → adversarial review). It cites real files, functions, tables, and config keys. Four of the analysis agents (detailed agent design + 3 verifiers) hit an account session limit and were authored/verified in-line by the lead instead; §9 is an explicit self-adversarial pass standing in for the deferred independent verification, which can be re-run later.

---

## 0. Executive summary

**The thesis: you already own the hard part, so do not rip it out.** AEO Studio is not a thin LLM wrapper waiting to be "made agentic." It is a mature, *deterministic-first* pipeline with clean, tool-shaped function seams: `build_site_profile`, `generate_blueprint`, `coverage_diff`, a 10-criterion scorer, `recommend`/`draft_missing_page`, and an independent validator — every one of which already returns a correct answer *with the LLM turned off*. It runs on a Postgres-backed job queue (`FOR UPDATE SKIP LOCKED`), has a retention moat ("Verified-live" outcomes that only flip when a re-crawled criterion's tier actually rises), and human-gated learning (overrides → *proposed* refinements, never auto-applied).

So "agentize" is the wrong frame if it means "8 autonomous agents." The right frame is: **keep the deterministic orchestrator as the controller, wrap the existing engines as tools, and spend frontier-LLM tokens only at the handful of genuine judgment seams — with every output flowing through approval gates that already exist in your schema.** That is the **Hybrid** architecture, and in the scored comparison it wins 24/25 against multi-agent (10), single-orchestrator (16), and event-driven (15).

**What this buys you:**
- **Cost discipline by construction.** ~$0.35 of frontier spend per *cold* audit, ~$0.06–$0.09 *warm* (the content-hash gate is the single biggest lever). ~$0.65 blended LLM/user/month. ~70–80% gross margin supports a $15–25/mo paid tier.
- **The moat survives.** A deterministic controller keeps week-over-week scores comparable (blueprint `content_hash` + versioning) — a nondeterministic agent-planner would quietly destroy that.
- **Trust is the product.** For an AEO tool that writes copy and claims onto client sites, *assistive + human gates* isn't a limitation — it's the only defensible MVP. A fabricated stat is a legal/reputational liability; the architecture makes "nothing publishes unreviewed" a structural invariant, not a policy.

**The honest challenge to your 8-agent list:** 3 are real LLM agents (**Planner, Research, Builder**). 2 collapse into one deterministic-plus-model-isolated **Critic** pipeline (Validator + Safety). 1 (**Monitoring**) is your existing retention loop + a scheduler, not a reasoning agent. 1 (**Deployment**) is a human handoff in MVP, a gated CMS-publish agent in V2. 1 (**UX**) is dropped as a per-run agent and absorbed by the Companion + existing scorers. Fewer, sharper agents = less cost, less nondeterminism, less surface to maintain — and it *is* the architecture the seams already imply.

**Effort:** ~34–42 engineer-weeks across 5 phases. Phase 1 (quick wins, ~3–4 wks) is purely additive and unblocks everything. Phase 2 (agent core, ~8–10 wks) is the spine. Phase 3 (gamified companion, ~5–6 wks) layers on after.

---

## 1. Current-state assessment (ground truth)

### 1.1 What the system is, in one diagram

```
                         ┌───────────────── Next.js wizard (web/) ─────────────────┐
                         │ page.tsx (4 steps→9-stage journey) · results.tsx        │
                         │ ScoreRing · PhasedPlanView · TodayTray · MilestoneDash   │
                         │ web/lib/api.ts (same-origin proxy, session_id cookie)    │
                         └───────────────┬─────────────────────────────────────────┘
                                         │ REST/JSON (X-API-Key)
                ┌────────────────────────▼──────────────────────────┐
                │ FastAPI  src/aeo/api/app.py  (~30 endpoints)        │
                │ auth · per-IP rate limit · SSRF guard · JobRegistry │
                └───┬─────────────────────┬───────────────────┬──────┘
        instant/sync│            async job│            in-memory│ JobRegistry (audit/deliverables)
                    │                     │  (Postgres jobs queue, FOR UPDATE SKIP LOCKED)
   ┌────────────────▼───┐   ┌─────────────▼───────────────────────────────────────────┐
   │ DETERMINISTIC CORE │   │ pipeline/orchestrator.py  audit_cycle / RUN_STAGES:      │
   │ plan_from_brief    │   │ discover → profile → blueprint → coverage → crawl →      │
   │ generate_blueprint │   │ analyze → report   (crawl async; analysis threaded)      │
   │ build_site_profile │   └───┬──────────┬───────────┬───────────┬──────────┬────────┘
   │ coverage_diff      │       │intelligence│reference │processor  │recommender│validation
   │ route_scenario     │       │site_profile│generator │gap/coverage│schema/    │independent
   └────────────────────┘       │business_int│framework │score_page  │content/   │adversarial
                                 │journey     │blueprint │(10 crit)   │draft/entity│perplexity
                                 └────────────┴──────────┴───────────┴───────────┴────────┘
                                         │ all pure, JSONB-serializable, LLM-optional
                ┌────────────────────────▼──────────────────────────┐
                │ PostgreSQL — 18 migrations (0001..0018)             │
                │ crawl_runs · crawled_pages · rubric_scores_v2 ·     │
                │ gap_analyses · recommendations · page_reports ·     │
                │ blueprints · coverage_diffs · jobs · events ·       │
                │ plan_states · implementation_milestones · outcomes ·│
                │ citation_results · criteria_refinements · agent_traces │
                └────────────────────────────────────────────────────┘
   LLM: nlp/llm.py  get_client() (frontier/cloud) · get_bulk_client() (Ollama qwen2.5:3b) · perplexity.py
```

### 1.2 The five facts that drive every design decision below

1. **The engines are pure functions with a deterministic floor.** Every LLM call site (`score_page`, `recommend`, `draft_missing_page`, `generate_blueprint`, `validate_page`) checks `llm.enabled` and falls back to a deterministic result; `LLMClient` returns `None` on any failure and never breaks a run. **→ An agent layer can wrap these as tools with zero risk of a hard failure.**
2. **The controller is already a correct state machine.** `orchestrator.audit_cycle` + `RUN_STAGES` sequence the pipeline deterministically. **→ Don't pay an LLM to re-derive control flow you already have and trust.**
3. **The human gates already exist.** `page_reports.review_status` (pending→approved/rejected) + `pending_review()` queue; `criteria_refinements` (proposed→accepted/rejected); milestone status *derived* from tasks with `status_source` (manual/crawl). **→ The approval workflow is a wiring job, not a greenfield build.**
4. **The moat is deterministic comparability.** Blueprint `content_hash` + versioning + the `outcomes.decide_status` "tier must rise on re-crawl" rule are what make week-over-week claims honest. **→ A nondeterministic planner would erode the one thing competitors can't fake.**
5. **The seams the agent layer must respect.** Identity is `url_normalized`/`domain`, **never `crawled_pages.id`** (it changes every run — FKs already `SET NULL`). The in-memory `JobRegistry` (500-cap, no persistence) cannot host minute/hour-long durable agents. The LLM client is a startup-locked `lru_cache` singleton with no per-call routing, no cost accounting, no fallback chain. Progress is poll-only (no SSE). Cancellation is per-page, not per-step.

### 1.3 The bottlenecks that *are* the work

| Bottleneck (file) | Why it blocks agents | Fixed in phase |
|---|---|---|
| In-memory `JobRegistry` (`api/jobs.py`, 500-cap, no persistence) | Durable multi-step agent runs can't live here | P2 (agent_runs table) / P4 (replace fully) |
| Startup-locked LLM client (`nlp/llm.py` `lru_cache`) | Hybrid per-call routing + cost trace impossible | P1 |
| Poll-only progress (no SSE) | Real-time agent narration (the copilot UX) has no transport | P1 skeleton / P4 full |
| Per-page-only cancellation (`should_cancel`) | A hung frontier call pins a worker indefinitely | P2 |
| No actor identity (`status_source` = manual/crawl only) | "agent acted vs human approved" is unrepresentable | P4 |
| `page_id` instability | Cross-run agent memory & streaks must key on `url_normalized` | P5 (stabilize) |
| Prompts hardcoded (only `content_depth.txt` is a file) | Agent layer multiplies prompt surface | P4 registry |
| Events best-effort, no dedupe/ordering | Unreliable as a gamification ledger | P3 (own table) |

---

## 2. Feature 1 — The Agentized AEO Studio

### 2.1 Architecture decision: Hybrid wins, decisively

| Option | Scal. | Cost | Lat. | Rel. | Maint. | **Total** | Verdict |
|---|---|---|---|---|---|---|---|
| (a) Multi-agent (8 autonomous) | 4 | 1 | 1 | 2 | 2 | **10/25** | Inverts a deterministic codebase into probabilistic actors; burns frontier tokens on exact math; erodes score comparability and the assistive+gates posture. **Reject.** |
| (b) Single orchestrator w/ tools | 3 | 3 | 3 | 3 | 4 | **16/25** | Tools map cleanly, but an LLM owning control flow re-derives `audit_cycle`, taxes the instant sync endpoints, and risks skip/dup steps (no idempotency token). The moment you carve out the fast paths, you've rebuilt (c). |
| **(c) Hybrid: deterministic controller + scoped LLM agents at decision points** | **5** | **5** | **5** | **5** | **4** | **24/25** | **Recommended.** It *is* the architecture the seams imply. |
| (d) Event-driven | 4 | 3 | 3 | 3 | 2 | **15/25** | Elegant for the retention/analytics fan-out later; as the primary architecture it needs a broker (violates "no new engine") and scatters a clean linear pipeline into hard-to-trace choreography. |

**Why (c):** It is the only option that satisfies all three locked constraints simultaneously. Deterministic control flow owns sequencing (preserving the moat); LLM output is confined to proposals/drafts that flow through gates that already exist and never auto-apply; frontier tokens are spent only at the ~5 real judgment seams the code already calls out (`business_intent` tiebreak, blueprint L3 augmentation, content/entity drafts, adversarial second-opinion, override→refinement). It slots scoped agents into the existing `FOR UPDATE SKIP LOCKED` queue with no new execution engine.

**Borrow from (d) later:** adopt the event pattern *only* for the retention/gamification fan-out (the `events` + `recommendation_outcomes` tables are already event-shaped) once the agent core exists.

### 2.2 The agent roster — challenging the 8-agent assumption

You asked me to challenge this, so here is the honest rationalization. The principle: **a thing is only an "agent" if it makes a genuine judgment under uncertainty using tools. Everything else is a deterministic capability the agent calls.**

| # | Your proposal | Decision | What it actually is | Wraps these existing seams | LLM tier |
|---|---|---|---|---|---|
| 1 | **Planner** | **KEEP (thin)** | Interprets messy user goals; assembles a typed task graph from deterministic plan/blueprint/coverage outputs; prioritizes. | `plan_from_brief`, `generate_blueprint`, `coverage_diff`, `route_scenario`, `build_site_profile` | Frontier (small, bounded). Deterministic floor: `route_scenario` already emits a full `StrategyPlan`. |
| 2 | **Research** | **KEEP (scope)** | Genuinely agentic: tool-using competitor + opportunity research, structural-pattern extraction (blueprint L1), SERP/Perplexity probes. | `competitor_discovery`, `framework_bootstrap`, `perplexity.cited`, crawler | Frontier for synthesis; cheap/local + deterministic HEAD-checks for verify. |
| 3 | **Builder** | **KEEP (core)** | The value engine: drafts pages, FAQ, entity rewrites, content edits — **staged, never published**. | `recommend`/`persist`, `draft_missing_page`/`draft_site_pages`, `content.py`, `schema.py` (deterministic), `packager.build_asset_bundle` | Frontier for prose; **deterministic for JSON-LD (never hallucinate schema)**. |
| 4 | **Validator** | **MERGE → Critic** | Not an autonomous agent — a gated quality pipeline run on every Builder output. | `validator.validate_page`, `independent.validate_independent` | Deterministic + 1 model-isolated LLM critic. |
| 5 | **Safety & Compliance** | **MERGE → Critic (+new)** | The highest-stakes gate for an AEO product: claim/stat verification + citation-hallucination + compliance flags, model-isolated from the generator. | `adversarial.adversarial_audit` (extend with a frontier **claim auditor**) | Frontier (isolated "refute this") + deterministic URL/JSON-LD checks. |
| 6 | **UX** | **DROP (absorb)** | No per-run value at MVP. Product-UX = analytics + the Companion; client-page-UX = existing `render_accessibility`/`answer_readability` scorers. | — (revisit V2 as optional page-UX critic) | — |
| 7 | **Deployment** | **DEFER → V2** | MVP: human ships the staged kit; `milestone verify` + re-crawl confirms live. V2: gated CMS publish (WordPress/Shopify via `cms_type`) with rollback. | `packager`, `milestones.mark_verified`, `outcomes.mark_from_recrawl` | None (MVP). |
| 8 | **Monitoring** | **KEEP (deterministic)** | Your existing retention loop + a scheduler — not a reasoning agent. Narration of it is ATLAS's job (Feature 2). | weekly cron, `outcomes.mark_from_recrawl`, `feedback.record_citation`, `events.metrics` | ~None. |

**Net MVP roster: 3 LLM agents (Planner, Research, Builder) + 1 Critic pipeline (deterministic + 2 model-isolated critics) + deterministic runtime capabilities (Monitoring scheduler, Deployment handoff).**

### 2.3 Communication & control flow — blackboard over Postgres, not a chat bus

A new **`AgentRunController`** (deterministic, in `src/aeo/agents/runtime.py`) is the controller — the agent-era sibling of `audit_cycle`. Agents do **not** message each other (the per-page Error-Sink isolation means they can't share mid-run state anyway). They read/write a shared run record; the controller sequences typed transitions.

```
 user goal ─▶ AGENT_RUN job enqueued (jobs queue, idempotency token)
                 │  worker claims via FOR UPDATE SKIP LOCKED
                 ▼
 ┌──────────────────────── AgentRunController (deterministic) ─────────────────────────┐
 │  step: PLAN ──▶ step: RESEARCH ──▶ step: BUILD ──▶ step: CRITIC ──▶ step: STAGE      │
 │   (Planner)      (Research)         (Builder)       (Critic pipe)    (→review queue)  │
 │      │              │                  │               │                 │           │
 │      ▼              ▼                  ▼               ▼                 ▼           │
 │   agent_steps rows (seq, agent, tool, input_hash, output_ref, model, tokens, cost,  │
 │   latency, error_class, status)  ── shared blackboard in agent_runs.payload JSONB    │
 └──────────────────────────────────────────────────────────────────────────────────────┘
                 │ each step has a DETERMINISTIC FLOOR — LLM failure degrades, never blocks
                 ▼
        HUMAN APPROVAL QUEUE (page_reports.review_status / new /api/agent/queue)
                 │ approve / edit / reject  per item
                 ▼
        human ships (MVP) ─▶ weekly re-crawl ─▶ outcomes.mark_from_recrawl (Verified-live)
                 ▼
        ATLAS narrates the verified win ─▶ agent_memory updated ─▶ loop
```

Transport: the existing **Postgres jobs queue** for run scheduling; the **`agent_runs`/`agent_steps`** tables as the blackboard. No broker, no message bus — matches the "extend worker + Postgres" constraint exactly.

### 2.4 State management

New, additive (mirrors `repos/jobs.py` + `repos/plan_state.py` patterns):

- **`agent_runs`** — `id, idempotency_key (unique), domain, client_id, url_normalized, blueprint_id (pin), status (queued/planning/researching/building/critiquing/staged/approved/rejected/failed/cancelled), current_step, payload JSONB, created_at, updated_at`. Status has **explicit terminal states** (the current queue's "dead" with no replay is a tech-debt to not repeat).
- **`agent_steps`** — `id, run_id (FK), seq, agent, tool, input_hash, output_ref, status, model, tokens, cost_usd, latency_ms, error_class, created_at`. This is both the resume log and the observability spine.
- **Resumability** reuses the `plan_state` pattern: a run resumes from its last good `agent_step` (like `plan_state_repo.latest_for_session`). Keys on `url_normalized`/`domain`, never `page_id`.

### 2.5 Memory architecture — three tiers

1. **Short-term run memory (working set):** `agent_runs.payload` + `agent_steps`. Scoped to one run; persisted for resume + audit.
2. **Long-term client/account memory (the differentiator):** new **`agent_memory`** table keyed on `domain`/`url_normalized` — durable facts the system should *remember* across weekly runs: decided business model + confidence (`business_intent`), accepted/rejected recommendations (`recommendation_outcomes`), captured overrides (`events`/`/api/overrides`), accepted criteria refinements, brand voice, known entities, prior verified wins. Feeds Planner/Builder so coaching and drafts get sharper over time. Ships **with a retention/GC policy from day one** (plan_states/events growing unbounded is existing debt — don't repeat it).
3. **Reference / world-model memory:** the versioned, pinned **blueprint** + framework + criteria definitions — already exists; it is what the system measures against. (V2/Enterprise: add a vector store for competitor-content retrieval — the codebase already flags embedding-based fuzzy match as the future upgrade to the token-overlap coverage diff.)

### 2.6 Approval workflow & HITL checkpoints

**Reuse the gates verbatim.** `recommendations.status` gains `'staged_by_agent'`; new `/api/agent/queue`, `/api/agent/approve`, `/api/agent/reject`; new `web/components/AgentReviewQueue.tsx`. Approvals ride `page_reports.review_status` + `pending_review()`. The learning gate stays `criteria_refinements` (proposed→accepted, never auto-applied — the circular-validation guard). Add `status_source='agent'` that **still routes through human/crawl verification** (an agent may never set `verified_completed` directly).

**Exact HITL checkpoints:**
1. **After Planner** — confirm goals/scope/priority (auto-proceed allowed for returning users with stored consent).
2. **After Builder + Critic** — every staged artifact (page draft, schema, FAQ, entity rewrite) sits in the review queue; human approves/edits/rejects per item.
3. **Before publish/deploy** — explicit human action. MVP: human ships. V2: human clicks "publish" on the CMS agent (per-action, with rollback).
4. **Criteria refinement (learning)** — override → proposal → human accept/reject.
5. **Safety escalation** — anything the Critic flags low-confidence or with an unverifiable factual claim is **force-routed to human**, never auto-approved.

### 2.7 Failure recovery

- **Idempotency:** `agent_runs.idempotency_key` (the DB queue lacks dedupe today — the in-memory registry has it; agents need it at the durable layer).
- **Per-step timeouts + async cancellation tokens** (current `should_cancel` is between-pages only — a hung frontier call must be killable mid-step).
- **Deterministic floor on every step** — every engine already returns a result with LLM off, so any LLM failure degrades gracefully to the deterministic output.
- **Retry + backoff** reuse `jobs.py` (1.5^n, cap 30s, max 4) with **explicit terminal states + replay** from the last good `agent_step`.
- **Error classification** — extend the Error Sink to triage *transient* (retry) vs *permanent* (skip+flag) vs *LLM* (fallback to deterministic), instead of swallowing indiscriminately.
- **Circuit breakers** — per-domain (stop pathological sites pinning workers) and **per-provider fallback chain** (frontier → secondary frontier → local Ollama), surfaced into `agent_traces` + an ops alert.
- **Validation retry** stays capped at 3 → then human review (existing pattern).

### 2.8 Observability

- **`agent_steps` + `agent_traces`** (the `agent` column is already a free-form VARCHAR) are the per-step trace store; extend `obs/tracing.trace_step` to log model / prompt-hash / tokens / latency / cost.
- **Cost-accounting wrapper on `LLMClient`** — today calls are fire-and-forget with silent `None` on failure; wrap to record token/$/latency per call (this is the #2 risk; build it in Phase 1).
- **`events` table** for product analytics — new `agent_action_*` event types, no schema change (JSONB metadata is flexible).
- **Real-time transport:** SSE skeleton on the existing `record_stage` fan-out in Phase 1; MVP UX uses cursor-based polling (the Companion is designed for both). Full per-step SSE in Phase 4. OTEL spans already accompany `trace_step`.

### 2.9 MVP / V2 / Enterprise

- **MVP (Phases 1–2):** 3-agent core + Critic pipeline on the existing worker + Postgres; durable `agent_runs`/`agent_steps`; human approval queue; per-call hybrid LLM routing + cost tracing; deterministic floors everywhere. Output: staged kit → review → human ships → crawl-verify. No CMS publish, cursor-poll (no SSE), single-vertical proof (PEV/Securin, per the v4 build sequence).
- **V2 (Phases 3–4):** ATLAS gamified companion over the live agent layer; SSE real-time narration; durable Postgres-backed job registry replacing in-memory; **CMS Deployment agent** (publish behind per-action approval + rollback); multi-step cross-page reasoning (loosen Error-Sink isolation with failure triage); versioned prompt registry; first-class actor identity (agent/human/crawl) on every mutation; Perplexity + adversarial gates ON by default for LLM-authored content.
- **Enterprise (Phase 5):** multi-tenant isolation + per-tenant crawl quotas + egress controls; distributed rate-limit + shared job store (Redis); horizontal worker autoscale; LLM cost budgeting/quotas + fallback chains + batching; retention/GC; hot-reload config + per-agent policy; `page_id` stabilization; SOC2-grade audit trail; team/RBAC; white-label companion (already designed into `/share`).

### 2.10 Cost model (hybrid LLM)

Assumptions grounded in config: `top_n=30` pages/audit; only 2 of 10 scorers are LLM (`content_depth`, `stats_in_html`) and they run on **local Ollama**; `num_predict=600`; `draft_limit=10`; validation `max_attempts=3`; Perplexity/adversarial off by default. Frontier blended ≈ $1.0/M effective.

| | Frontier tokens | Frontier $ |
|---|---|---|
| **Cold audit** (all 30 pages billed) | ~327k (recommend/draft ~281k + agent plan/research ~46k) | **~$0.35** |
| **Warm audit** (content-hash gate hot, ~10–20% pages) | ~40k | **~$0.06–$0.09** |
| **Per active user / month** (1 cold + 3 warm + 5 draft bursts + ~30 copilot turns) | ~612k | **~$0.55–$0.75** |

| Scale | LLM/mo | Infra/mo | Total | $/user |
|---|---|---|---|---|
| 100 users | ~$65 | $250–400 (1 API + 1 worker + PG + 1 Ollama box) | ~$315–465 | $3.15–4.65 |
| 1,000 users | ~$650 | $1.8k–2.8k (+Redis **mandatory**, HA PG, 1–2 GPU) | ~$2.45k–3.45k | $2.45–3.45 |
| 10,000 users | ~$6,500 | $9k–16k (autoscale, read replicas, GPU pool — local tier now a real line item) | ~$15.5k–22.5k | $1.55–2.25 |

**Biggest drivers (ranked):** cold recommend+draft loop → validation retry loop → unbounded interactive copilot → local-GPU at 10k → drafts. **Controls (all already seamed):** content-hash gate (15–20% of cold), model routing (`bulk_provider`→local), deterministic-first (`use_llm=false` default = $0 instant plan), blueprint pinning, **batch the ~145 recommend calls/audit into ~30 by grouping deficient criteria per page**, lower `max_attempts` for low-priority pages, cap copilot turns/user/day.

**Pricing implication:** never flat. Base = instant deterministic plan ($0) + **metered frontier credits** for personalize/drafts/copilot; gate audit cadence, `draft_limit`, copilot budget, and Perplexity/adversarial behind tiers; bill enterprise on **pages-scored / change-volume** (aligned with the content-hash gate's real cost), not seats. **Instrument per-call cost before scaling** — whale detection is blind without it.

---

## 3. Feature 2 — ATLAS, the gamified AI companion

### 3.1 Design thesis: reward verified outcomes, never activity

The brief's risk is "childish." The fix is structural, not cosmetic: **every reward joins to a real verdict table the retention metrics already read** (`recommendation_outcomes`, `milestone_tasks` crawl-verified, `citation_results`, `coverage_diffs`, `rubric_scores_v2`). The companion *narrates verdicts; it never authors them.* This makes it impossible for the game layer to tell a happier story than reality — the single thing that makes gamification feel respectful to a founder/agency rather than a toy.

Reference points: **Linear / Stripe / GitHub / Vercel / Cursor** (progress, momentum, mastery, status) — not Duolingo's mascot + guilt loop. Working name **ATLAS** (maps/coverage; pairs with "blueprint"); alternatives CITE / MERIDIAN / VANTAGE / NORTH; white-labelable on `/share`.

### 3.2 User journey (layered onto the existing flow, gating nothing)

A thin narration + coaching rail attached to each stage. Stage 1 (Website): narrates the real `/api/profile` crawl via `PrefillProgress` + the `ScoreRing` reveal ("38 — Barely visible"). Stages 2–4 (About/Competitors/Goals): CSM + Coach explaining what each field means for AEO and mapping goals → rubric criteria. **Stage 5 (Analysis): the marquee moment** — ATLAS subscribes to the audit job's real per-stage progress + `agent_traces` and narrates actual work ("/platform: schema_markup tier 4, content_depth tier 2 … coverage 6/11"). Stages 6–8: blueprint explainer, the phased plan as a **quest log** (TodayTray = active quest), handoff framed as "shipping" + the return hook. **Stage 9 (return): the compounding loop** — leads with verified-live wins from `outcomes.implemented_for_domain`.

### 3.3 Gamification framework & reward systems

**Three reward axes — the only things that earn anything:**
1. **Coverage** (a missing blueprint node goes live and is crawl-detected) — source `coverage_diffs` + `milestone_tasks(crawl)`.
2. **Verified-live wins** (the moat) — `recommendation_outcomes.status='implemented'`, which flips *only* when the targeted criterion's tier rises on re-crawl. Highest value, hardest to game.
3. **Citations earned** — `citation_results.cited=true` (Perplexity). The terminal goal.

**Four reward types, escalating:** *Acknowledgments* (manual "in progress" — conversational, writes nothing); *Momentum* (a quiet ember; only moves on verified outcomes; decays slowly in neutral language — "cooled," never "you lost your streak"); *Status tiers* (GitHub/Linear-flavored credentials: "Foundation Solid," "Recommended for CTEM platforms," "First Citation" — shareable on `/share`); *Verified-Win cards* (the celebration: criterion + page + tier delta + score impact). **Anti-inflation:** awards are idempotent on the source verdict id (re-running a crawl never double-grants); regressions reverse the momentum they granted; **nothing is ever awarded on `status_source='manual'`.**

**The score is the scoreboard.** The canonical `aeoScore` (0–100, `web/lib/score.ts`) and its band ("Barely visible → On the radar → Recommended → Top answer") is the only headline number. No parallel vanity XP currency that could diverge from reality — momentum is explicitly secondary.

### 3.4 Progression — a site-maturity ladder, not character levels

The "level" is the site's real maturity: the existing `scenario` taxonomy (`NO_WEBSITE → SINGLE_PAGE → SMALL_SITE → GROWING_SITE → MATURE_SITE`) crossed with the score band → 5 stages (Foundations → On the Radar → Recommended → Authority → Cited Leader). **Three skill tracks** mirror the rubric families: **Structure** (schema/heading/render/load), **Answerability** (qa_blocks/readability/depth/stats), **Authority** (entity/citation_signals + `citation_results`). A track advances *only* when a re-crawl confirms higher tiers. **Quests = the existing phased plan** (`build_plan` week_1/week_2_4/later) — no invented content; a quest completes when its `milestone_tasks` reach `verified_completed`. Progression is **reversible and honest** — a regression drops a band, framed as "a page slipped" with a concrete re-fix, never punishment.

### 3.5 Personality & sample conversations

One voice, three modes (CSM / Coach / tasteful Celebrant). Register: a sharp senior growth/SEO operator who lives in the product — peer-to-peer, concrete (cites the actual page/criterion/tier/delta), calm, honest about setbacks. Essentially no emoji (matches the codebase); exclamation points reserved for genuine verified wins.

> **Coach (why a task matters):** "This is your highest-leverage move. /pricing scores tier 2 on `qa_blocks` and `answer_readability` — the two criteria answer engines lean on hardest for 'how much does X cost', your highest-intent query. Ship the Q&A block I drafted and I'd expect tier 4 on both, ~+3 to your AEO Score. Your top competitor already has this — it's why they get named and you don't yet."

> **Verified win (return visit):** "Welcome back — good news first. Your FAQ block on /pricing went live and my re-crawl verified it: `qa_blocks` moved 2→4. That's a real win, not a checkbox. [Verified-Win card · +3 AEO] You're at 61 — that crosses into 'Recommended.' One more verified fix on /platform and you'd lead your category."

> **Honest regression (no guilt):** "Heads up — one thing slipped. /solutions lost its FAQ schema (a template change stripped the JSON-LD), so `schema_markup` fell 4→2 and your score dipped a point. Not a crisis — it's the exact snippet from your kit. Re-paste it and the next crawl restores the win. I've put it back at the top of Today."

### 3.6 Wireframes (ASCII)

```
COLLAPSED (founder in a hurry):
[ ATLAS · AEO 61 'Recommended' · 2 verified wins · momentum warm   ^ expand ]

VERIFIED-WIN CARD (single decisive reveal — no confetti):
┌────────────────────────────────────────────┐
│  VERIFIED LIVE                              │
│  /pricing · qa_blocks   tier 2 → 4          │
│  +3  AEO Score    (now 61 · Recommended)    │
│  "The kind of page AI assistants quote."    │
│                          [ what's next > ]  │
└────────────────────────────────────────────┘

MATURITY / SKILL TREE (GitHub-graph restraint):
┌────────────────────────────────────────────┐
│ MATURITY:  On the Radar → [RECOMMENDED] →   │
│            Authority → Cited Leader         │
│ STRUCTURE     ████------  3/4 tier 4+       │
│ ANSWERABILITY █████-----  2/4 (qa_blocks↑)  │
│ AUTHORITY     ██--------  1/4 (citation …)  │
│ Earned: Foundation Solid · Half Covered     │
└────────────────────────────────────────────┘
```

### 3.7 Database & backend

**Additive migrations 0019–0021** (mirroring conventions — BIGSERIAL, JSONB, `set_updated_at()` trigger, `session_id` identity): `gamification_state` (maturity_stage, aeo_score snapshot, momentum, last_verified_at, verified_wins, citations_earned, track_progress JSONB), `gamification_awards` (append-only ledger with **`UNIQUE(award_type, source_table, source_id)`** — the anti-inflation guard), `achievement_definitions` + `achievement_unlocks` (declarative, bound to real metrics, earn-once). Quests reuse `implementation_milestones`/`milestone_tasks` as-is. Companion analytics flow through the existing `events` table with new event types — **except** the reward ledger, which is its own transactional table (the `events` table is best-effort and unfit as a ledger).

**Backend module `src/aeo/companion/`** — three thin seams: **`narrator.py`** (turns real job stages + `agent_traces` into prose via cursor-based `GET /api/companion/narration` — never a fake bar), **`rewards.py`** (idempotent reconciler reading verdict tables; runs at audit completion + on companion load; `decide_status` remains the sole verdict authority), **`coach.py`** (deterministic facts from `GapResult`/`CoverageDiffResult`/`StrategyPlan`; frontier LLM only *phrases* — never invents a number; templated fallback when LLM off). New `web/lib/companion.ts` + `CompanionRail` reusing `motion/primitives.tsx` (CountUp/Tally/Reveal) + `ScoreRing`.

---

## 4. Phase 2 — Codebase impact analysis

| Feature | Files affected (real paths) | Complexity | Risk | Dependencies |
|---|---|---|---|---|
| **A. Agent-run state schema** | NEW `migrations/0019_agent_runs.sql` (+0020 idx); NEW `storage/repos/agent_runs.py`, `agent_memory.py`; `storage/models.py` (+dataclasses) | Med | Low | None — additive; foundation for the track |
| **A. In-repo durable agent runtime** | NEW `agents/{__init__,runtime,steps}.py`; `pipeline/worker.py` (+`AGENT_RUN` kind); `storage/repos/jobs.py` (per-kind filter); `cli.py` (`aeo agent`); `settings.py` (`AgentCfg`) | **High** | **High** | A-schema. MVP spine; reuses `claim()` (no broker). Add per-**step** cancellation. |
| **A. Planner agent** | NEW `agents/planner.py`; reuses `intelligence/brief.py`, `reference/generator.py`, `processor/coverage_diff.py`, `intelligence/scenario.py`; `api/app.py` (+`POST /api/agent/plan`) | Med | Low | runtime. Seams already pure/JSONB. |
| **A. Research/Builder agent** | NEW `agents/builder.py`; reuses `recommender/{__init__,draft,content,schema}.py`; `storage/repos/recommendations.py` (+`status='staged_by_agent'`); migration 0021 (status enum) | Med | Med | runtime + schema. Inherits LLM sync/no-batch debt. |
| **A. Human approval gate** | `storage/repos/reports.py` (`set_review_status` extend), `feedback.py` (reuse); NEW `api/app.py` `GET /api/agent/queue`, `POST /approve|/reject`; NEW `web/components/AgentReviewQueue.tsx`; `web/lib/{api,types}.ts` | Med | Med | Builder. Reuses existing human-gate pattern; **is** the assistive+gate constraint. |
| **A. Per-call LLM routing + cost trace** | `nlp/llm.py` (per-call provider override; remove `lru_cache` hard-lock; trace hook); `settings.py` (+`planning_provider`); `obs/tracing.py` (model/tokens/latency); NEW `migrations/0022_llm_call_traces.sql` | Med | Med | Unblocks hybrid posture; provider currently locked at startup. |
| **A. Real-time step streaming (SSE)** | `api/app.py` (`GET /api/agent/{id}/stream`); `api/jobs.py` (`record_stage` subscriber fan-out); `web/lib/api.ts` (EventSource); `web/components/results.tsx` (reuse `AnalysisProgress`) | High | Med | runtime. Progress is poll-only today. |
| **A. Durable job registry (replace in-memory)** | `api/jobs.py` (back agent jobs with `agent_runs`); `storage/repos/jobs.py`; `api/app.py` (spawn paths) | High | **High** | A-schema. The in-memory 500-cap registry is the #1 scale blocker. |
| **G. XP/level/streak/achievement schema** | NEW `migrations/0023_gamification.sql`; NEW `storage/repos/gamification.py`; `storage/repos/events.py` (emit) | Med | Low | None — additive; hooks `outcomes`/`milestone_tasks`. |
| **G. XP award engine (verified wins)** | `storage/repos/outcomes.py` (`mark_from_recrawl` +hook), `milestones.py` (`mark_verified` +hook); `gamification.py`; `pipeline/orchestrator.py` (`_verify_milestones`) | Med | Med | G-schema. Honor the Verified-live moat. |
| **G. Companion UI (XP bar, toasts, level-up)** | NEW `web/components/{GamificationLayer,AchievementToast,XpBar}.tsx`; `results.tsx` (wire micro-rewards); `motion/primitives.tsx` (reuse); `web/lib/{api,types,score}.ts` | Med | Low | G-schema + engine + `GET /api/gamification`. After agent core. |
| **G. Gamification API** | `api/app.py` (`GET /api/gamification`, `/leaderboard`, `POST /claim`); `web/lib/api.ts` | Low | Low | G-schema. Mirrors `/api/metrics`. |
| **G. Citation/win leaderboard** | `storage/repos/feedback.py` (aggregate `recent_observations`); `gamification.py`; `api/app.py` | Low | Low | G-schema. Read-side only. |
| **X. Agent+gamification telemetry** | `storage/repos/events.py` (new event types, no schema change); `obs/tracing.py`; `storage/repos/traces.py` | Low | Low | None — free reuse. |

**Reusable components (the new work rides these):** `repos/jobs.py` (the queue — no new broker), `pipeline/worker.py` (add a kind), `repos/plan_state.py` (resumable-state model), the three recommender generators + `draft_missing_page` (pure), the deterministic planning seams (`plan_from_brief`/`generate_blueprint`/`coverage_diff`/`route_scenario`), `repos/feedback.py` + `repos/reports.py` (human-gate substrate), `repos/outcomes.py` (Verified-live moat), `repos/events.py` (telemetry sink), `nlp/llm.py` (DI'd facade with deterministic floor), `web/components/results.tsx` + `motion/primitives.tsx` + `web/lib/score.ts`.

**Architectural bottlenecks** (see §1.3) · **Tech debt to retire along the way:** in-memory rate limiter + job registry → shared store; hardcoded prompts → versioned registry; append-only `recommendations` (no unique key) → add `agent_run_id` + latest-view; best-effort `events` → transactional gamification ledger; unbounded `plan_states`/`plan_shares` → TTL/GC; `lru_cache` configs → TTL/clear hook; dual-tracked frontend progress (localStorage + best-effort server) → awaitable sync + etag.

---

## 5. Phase 3 — Implementation roadmap

### Phase 1 — Quick wins & foundations (~3–4 eng-weeks) · risk: Low
- **Goals:** unblock everything; ship zero-risk additive scaffolding.
- **Deliverables:** `agent_runs`/`agent_steps`/`agent_memory` + gamification migrations & repos; agent-action + gamification event types wired into `events`/`agent_traces` (no schema change); **per-call LLM provider override + call-level cost trace** (the hybrid unlock + cost visibility); SSE skeleton on the `record_stage` fan-out.
- **Risks:** none material (additive). **Effort:** 3–4 wks.

### Phase 2 — Agent core (~8–10 eng-weeks) · risk: High (highest value)
- **Goals:** prove the assistive-copilot loop end-to-end on one vertical.
- **Deliverables:** durable in-repo agent runtime on the Postgres queue (`AGENT_RUN` kind, plan→research→build→critic→stage loop, **step-level timeouts + cancellation**); Planner over the deterministic seams; Research/Builder over the recommender generators (staged output); the human approval queue (`/api/agent/queue` + `AgentReviewQueue.tsx`) reusing `review_status` + the refinement pattern; the Critic pipeline (deterministic checks + model-isolated adversarial + claim/safety audit).
- **Risks:** runtime complexity; cost-loop amplification (mitigate with cost trace from P1 + per-criterion batching + turn budgets); prompt-injection from crawled competitor content into frontier agents (sanitize before `generate_json`; keep schema deterministic). **Effort:** 8–10 wks.

### Phase 3 — Gamified companion (~5–6 eng-weeks) · risk: Med
- **Goals:** wrap ATLAS around the live agent layer for engagement + retention.
- **Deliverables:** XP/level/streak/achievement engine wired to **re-crawl-verified outcome flips only**; `src/aeo/companion/{narrator,rewards,coach}.py`; companion UI (XpBar/AchievementToast/level-up + CompanionRail) reusing motion primitives + ScoreRing; gamification + leaderboard API; citation leaderboard from `feedback`.
- **Risks:** "childish" perception (mitigated by the verified-outcome-only design); double-grant (idempotent ledger). **Effort:** 5–6 wks.

### Phase 4 — Advanced orchestration (~8–10 eng-weeks) · risk: High
- **Goals:** make it durable, real-time, and multi-step at scale.
- **Deliverables:** replace the in-memory `JobRegistry` with Postgres-backed durable runs for **all** long jobs; end-to-end per-step SSE; cross-page multi-step reasoning (loosen Error-Sink isolation with failure-class triage); priority lanes + idempotency tokens on the queue; versioned prompt registry; **first-class actor identity (agent/human/crawl) on every mutation**; CMS Deployment agent (publish behind per-action approval + rollback).
- **Risks:** significant rework of synchronous/in-memory/page-isolated seams; CMS publish is genuinely risky → strong gates + rollback. **Effort:** 8–10 wks.

### Phase 5 — Enterprise scaling (~10–12 eng-weeks) · risk: High
- **Goals:** multi-tenant, cost-bounded, horizontally scalable.
- **Deliverables:** distributed rate limit + shared job store (Redis); multi-worker autoscale; LLM cost budgeting/quota + fallback chains + batching; retention/GC on `agent_memory`/`plan_states`/`events`; hot-reload config + per-agent policy; `page_id` stabilization (stable content signature); per-tenant crawl quotas + egress controls; SOC2-grade audit trail; team/RBAC; white-label companion.
- **Risks:** distributed-systems complexity; data migration for `page_id` stabilization. **Effort:** 10–12 wks.

**Total: ~34–42 eng-weeks.**

---

## 6. Risk register (top items, with mitigations grounded in existing machinery)

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| 1 | **Hallucinated claims published on client sites** (false stat/claim/JSON-LD) — legal/reputational liability, trust-killer | **Critical** | Keep `schema.py` deterministic (JSON-LD never hallucinated); enforce the human-approval gate (nothing publishes unreviewed; deploy human-triggered); turn ON Independent Validator + Adversarial Auditor (model-isolated "refute this" + citation-hallucination URL checks) for every LLM-authored draft; route low-confidence to human. |
| 2 | **Cost blow-up** — unbounded copilot + `max_attempts=3` retry, no circuit breaker, silent `None` failures | High | Cost-accounting wrapper on `LLMClient` → `agent_traces`; per-user/day frontier turn budgets; lower `max_attempts` for low-priority pages + short-circuit on stationary deterministic recommender; copilot deterministic-first; frontier reserved for paid tiers. |
| 3 | **In-memory registry + per-process rate limiter** don't survive multi-worker scale | High | Back both with Redis before 1k users; migrate audit/deliverables onto the Postgres queue with idempotent enqueue tokens. |
| 4 | **Content-hash gate defeated** by high-change sites (warm→cold economics) + uncancellable hung LLM call pins a worker | High | Per-domain change-rate cap (bill/throttle); hard per-page + per-call timeouts + finer cancellation; per-domain circuit breaker. |
| 5 | **Frontier provider dependency** — silent failure, rate limits, price changes, no HA fallback | High | Fallback chain frontier→secondary→local (reuse dual-client pattern); surface failures to `agent_traces` + alert; deterministic-first already guarantees the run never breaks — make degradation observable. |
| 6 | **Agent overstep** — an over-eager agent marks tasks verified / pushes changes | High | Milestone status stays DERIVED + only crawl/manual flips it; add `status_source='agent'` that still routes through verification; keep `criteria_refinements` human-gated; owner `manual` beats agent writes. |
| 7 | **Circular validation / self-grading** | Med | Independent Validator already decouples from the rubric; enable Perplexity citation test for high-value pages; feed `citation_results` into human-gated refinements; keep re-score on the disabled-LLM client. |
| 8 | **`page_id` instability** → dangling FKs, pending-forever outcomes | Med | Key identity on `url_normalized` everywhere; add a give-up/timeout status to `recommendation_outcomes`; enforce `rubric_version` at ingestion. |
| 9 | **SSRF / crawl abuse** at multi-tenant scale | Med | Keep `_assert_crawlable_host` at every entry + redirect hop; per-tenant crawl quotas + egress controls + Redis-backed distributed limiter. |
| 10 | **Stale config in long-lived workers** (`lru_cache`) desyncs scoring across the fleet | Med | Config fingerprint stamped per run; refuse to mix versions in a cohort; cache-clear/TTL or rolling restart; pin runs to `blueprint_id`. |
| 11 | **Unbounded growth + best-effort analytics** corrupts pricing/whale decisions | Med | TTL/GC for `plan_states`/revoked `plan_shares`; make cost/override/outcome analytics at-least-once via the durable queue; surface a "metrics incomplete" health signal. |

---

## 7. What I changed about your plan (and why)

1. **8 agents → 3 + a Critic pipeline + deterministic capabilities.** Most of your proposed agents wrap functions that need *zero* LLM. Making them "autonomous agents" burns frontier tokens (cost 1/5), stacks latency, and injects nondeterminism that erodes your score-comparability moat. Fewer, sharper agents is strictly better here.
2. **No autonomous control loop.** Even a single-orchestrator LLM (the runner-up at 16/25) would re-derive a state machine you already have and trust. The deterministic controller stays; the LLM only decides at named seams.
3. **Deployment is a human handoff in MVP.** Your "Deployment Agent" publishing to client sites is a V2 capability gated behind per-action approval + rollback — not because it's hard to build, but because for an AEO product, auto-publish is where trust dies.
4. **UX Agent dropped.** Its useful intent is the Companion (product UX) + existing `render_accessibility`/`answer_readability` scorers (client-page UX). A per-run "UX agent" adds latency and cost without a distinct job.
5. **Gamification rewards verified outcomes, never activity.** The only design that survives a skeptical founder. It also happens to be free of a parallel currency that could drift from your canonical `aeoScore`.
6. **Cost is a first-class Phase-1 deliverable, not an afterthought.** You currently have *zero* per-call cost accounting and silent failures. At thousands of users with an engagement-maximizing copilot, that's the difference between 70% margin and a surprise five-figure bill.

---

## 8. Open decisions for you

1. **Frontier provider** for the reasoning/Builder tier (Claude vs. GPT vs. Gemini-Pro) — affects the cost blend and the fallback chain. (Hybrid posture is locked; the specific vendor isn't.)
2. **First vertical for the MVP slice** — the v4 build sequence says PEV/Securin; confirm, or pick the highest-revenue vertical.
3. **CMS publish scope for V2** — WordPress + Shopify first (you already store `cms_type`), or broaden.
4. **Companion default density** — opt-in rail vs. on-by-default for new users (recommend on-by-default, collapsible).
5. **Audience for this doc's next cut** — if you want an investor/board version, I'll produce a strategy-forward 3-pager from this.

---

## 9. Adversarial self-review (stands in for the deferred verifier agents)

The three independent verifier agents hit the session cap; here is the honest skeptic's pass I would have asked them for. **Verdict: sound, with the following caveats to address during build.**

**Strongest objections & corrections:**
- **"You're under-selling the runtime risk."** True — the durable agent runtime (Phase 2) + replacing the in-memory registry (Phase 4) are the two High-risk, High-complexity items and they're load-bearing. *Correction:* sequence the `agent_runs` schema + cost trace + SSE skeleton in Phase 1 so Phase 2 is "fill in the loop," not "invent the substrate." Don't start Builder before the approval queue exists.
- **"Idempotency is hand-waved."** The Postgres queue has no dedupe today (only the in-memory registry does). *Correction:* `agent_runs.idempotency_key UNIQUE` is non-optional and must land in Phase 1, or retried/duplicated enqueues will double-bill frontier tokens.
- **"Prompt-injection from crawled competitor content is a real attack surface."** Frontier agents ingest competitor HTML; a hostile page can try to steer drafts. *Correction:* treat all crawled text as untrusted; sanitize before `generate_json`; keep JSON-LD deterministic; the model-isolated Critic is a second line, not the only one.
- **"Multi-tenant isolation is a Phase-5 footnote but a Phase-2 reality the moment you have 2 paying customers."** *Correction:* even MVP must scope every agent query by `client_id`/`domain` and never let one run read another's memory — cheap to do early, expensive to retrofit.
- **"The gamification 'honesty' claim depends on Perplexity/adversarial being ON, which they aren't by default."** *Correction:* citations are an axis of reward; enabling `perplexity.enabled` for high-value pages (with a `(question,target_url)` cache to control cost) is a prerequisite for the "Citations earned" reward to mean anything.
- **"Effort estimates are optimistic on the frontend."** The dual-tracked progress (localStorage + best-effort server) and the two separate rendering paths (`MilestoneDashboard` vs `PhasedPlanView`) mean agent-activity UI is duplicated unless unified first. *Correction:* add ~1–2 wks in Phase 3/4 to unify the progress-component vocabulary.

**What to keep (high confidence):** the Hybrid decision; reusing the human gates verbatim; the verified-outcome-only gamification; cost accounting as a Phase-1 deliverable; the rationalized agent roster. These are grounded in the real seams and are the lowest-risk, highest-reuse path.

*Independent multi-agent verification can be re-run once the session limit resets (the workflow is resumable: cached agents return instantly, only the 4 failed ones re-run).*
