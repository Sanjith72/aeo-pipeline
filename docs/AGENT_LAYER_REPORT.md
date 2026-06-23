# AEO Studio — Agent Layer & Gamification: What Was Built and Why

*A plain-English report covering everything added across Phase 2 (plans 2A–2D) and Phase 3.*

---

## 1. The one-paragraph summary

AEO Studio looks at a website and tells it how to get **recommended by AI answer engines**
(ChatGPT, Perplexity, Google's AI answers). Before this work, the product could *analyze* a site
and hand a human a plan — but a person had to do all the actual work. This project added an
**AI "agent" layer that does the work itself** — it researches competitors, plans the ideal site,
writes draft pages, and checks its own writing — but **always stops and waits for a human to
approve before anything is used**. It also added an **honest rewards system** that only celebrates
real, verified improvements. All of it was built on the existing database and job queue, so it
needed **no new servers**.

---

## 2. How it works, in 30 seconds

When you start an agent run, four AI workers act in sequence, and then it stops for you:

```
You start a run
      │
      ▼
1) RESEARCH  →  finds your real competitors and checks each is a live website
      │
      ▼
2) PLANNER   →  decides what pages your ideal site should have (your gaps)
      │
      ▼
3) BUILDER   →  writes draft pages: copy + FAQ + the behind-the-scenes data search engines read
      │
      ▼
4) CRITIC    →  reviews each draft for quality and flags risky claims
      │
      ▼
   STAGED  →  everything lands in the Review Queue (the /agents page)
      │
      ▼
   YOU approve or reject each run  →  nothing is ever published until you do
```

The **golden rule baked into the code**: agents *propose*, humans *approve*. Nothing the AI writes
goes live on its own.

---

## 3. Plan-by-plan: what was added, and why

### Plan 2A — The agent runtime "skeleton"

**What it added:** the plumbing. A durable "agent run" that is saved in the database (so it
survives restarts), a deterministic Planner that turns a business into a to-do list of pages, the
wiring to run it on the existing background job queue, and the API + command-line tool to start a
run, check it, and **approve / reject** it. Every run ends in a `staged` state, waiting for a human.

**Why it was added / problem it solves:** you can't build smart AI agents without a reliable,
restartable backbone first. 2A proved the whole **"agent proposes → human approves"** loop works
end to end — deliberately with **no AI yet**, so it's fast, cheap, and fully testable. It also
proved we needed **zero new infrastructure** (it reuses the existing Postgres job queue).

**What it brings to the table:**
- Agent runs that don't vanish on a crash (durable state + a step-by-step trace).
- The human-approval gate as a **hard rule in code**, not an optional setting.
- A clean foundation everything else plugs into.

*Key pieces:* `agent_runs` / `agent_steps` tables (migration 0019), `AgentRunController`,
`POST /api/agent/run`, the `aeo agent` CLI command, the `AGENT_RUN` worker job.

---

### Plan 2B — Research + Builder agents + "hybrid" AI

**What it added:** the first *real* AI. A **Research agent** that discovers competitors and verifies
each domain is a live site, and a **Builder agent** that writes the actual draft pages. Plus a
"hybrid" setup that uses a smart (more expensive) model only for writing/reasoning and cheaper
models for grunt work — and **tracks the cost of every AI call**.

**Why it was added / problem it solves:** the skeleton had no intelligence. This makes the agents
genuinely useful — they produce real, usable drafts. The **cost tracking** solves a critical
blind spot: *"we have no idea how much each AI run costs."* Knowing the tokens and dollars per run
**before** scaling up is how you avoid surprise bills and detect runaway usage.

**What it brings to the table:**
- Real drafted pages (copy, FAQ, structured data) staged for review.
- Every AI call's tokens, cost, and latency recorded on the run.
- Smart model routing so you don't overpay for simple work.

**Safety built in:** drafts are **staged, never published**, and the structured data (the
machine-readable JSON-LD) is **always built by code, never written by the AI** — so it can't be
hallucinated or faked.

*Key pieces:* `get_planning_client` (the frontier tier), `nlp/cost.py` (cost estimates),
`InstrumentedLLM` (the cost recorder), `agents/research.py`, `agents/builder.py`.

---

### Plan 2C — The Critic (quality + safety gate)

**What it added:** an automatic reviewer that checks every draft three ways:
1. **Independent check** — is the page well-formed and answerable?
2. **Adversarial auditor** — a *separate* AI told to "try to refute this," which catches
   **fake or made-up citations/sources**.
3. **Claim auditor** — flags **risky marketing claims** ("#1", "the leading", "99% guarantee",
   specific stats) that a human must verify before publishing.

**Why it was added / problem it solves:** AI sometimes writes confident nonsense — invented
statistics, sources that don't exist. For a tool that writes **claims onto a client's website**,
that's a real **legal and reputation risk**. The Critic catches these and **flags them for a
human** — but it **never auto-rejects and never publishes**. It doesn't replace the human; it makes
the human's review faster and safer.

**What it brings to the table:**
- Trust. Each draft now carries a verdict — **"Looks clean"** or **"Needs review"** — plus a list
  of specific claims to double-check.
- A separation between the AI that *writes* and the AI that *checks*, so they don't rubber-stamp
  each other.

*Key pieces:* `agents/critic.py` (`review_drafts`, `claim_audit`), reusing the existing
independent validator and adversarial auditor.

---

### Plan 2D — The Review Queue screen + live updates

**What it added:** the human-facing page (`/agents`). It lists the staged runs, **streams each
run's steps live** as the agents work, shows every drafted page with its Critic badge, and provides
the **Approve / Reject** buttons.

**Why it was added / problem it solves:** 2A–2C built all the backend machinery, but a person needs
an actual screen to *see and act on* it. This is that screen — the one you review work on. The live
streaming turns "wait and refresh" into "watch the agents work."

**What it brings to the table:**
- The approval gate becomes usable by a real person, not just an API call.
- Real-time visibility into what the agents are doing.

*Key pieces:* the `/agents` route, the `AgentReviewQueue` component, `GET /api/agent/runs` (the
queue) and `GET /api/agent/run/{id}/stream` (live updates via Server-Sent Events).

---

### Plan 3 — The honest gamification engine

**What it added:** a rewards system that **only counts real, verified wins**. A reward is granted
only when a fix you made is confirmed by a **re-crawl** to have actually improved a score — never
for clicking around or toggling a checkbox. It tracks "verified wins," a **maturity ladder**
(Foundations → On the radar → Recommended → Authority), and momentum, shown in a small status strip.

**Why it was added / problem it solves:** most gamification rewards *activity* — vanity points you
can rack up without achieving anything, which is hollow and easy to game. This rewards **only proven
outcomes**, so the numbers stay honest and actually motivate users to **implement and verify** real
changes. Importantly, your **AEO Score stays the single headline number** — there's no fake
parallel "points" currency competing with it.

**What it brings to the table:**
- Motivation tied to results, not busywork.
- Rewards that are **idempotent** — re-running the math never double-counts or inflates a win.

*Key pieces:* migrations 0020–0022 (`gamification_state`, `gamification_awards`, `achievements`),
`companion/rewards.py` (the reconciler), `/api/gamification`, the `GamificationStrip` component.

---

## 4. The principles that run through all of it

- **Deterministic-first:** every agent step has a non-AI fallback. If the AI is slow, down, or
  fails, the step quietly degrades to a sensible default — it **never crashes or blocks** a run.
- **Assistive + human-gated:** agents stage proposals; a human approves. The Critic flags but never
  auto-rejects or publishes. This is a structural guarantee, not a policy.
- **Honest rewards:** progress is only credited when a re-crawl verifies it actually happened.
- **No new infrastructure:** everything reuses the existing Postgres database and job queue.

---

## 5. Fixes made along the way (not in the original plans, but necessary)

- **Job-queue bug:** found and fixed a pre-existing bug where the background worker's
  "claim a job" query had its parameters in the wrong order — which silently broke **every** worker
  that filtered by job type (not just the new agent jobs). Added the first test for it.
- **Docker:** moved the worker into the default stack so `docker compose up` processes agent runs
  out of the box, and documented the new settings in `.env.example`.
- **UI crash + styling:** the page cost value came back from the database as text, and the UI did
  math on it — fixed the number handling, and re-themed the agent screens to match the app's dark
  design instead of generic grey.

---

## 6. How to run it

1. `docker compose up -d --build` — brings up the database, applies all migrations, and starts the
   API, web UI, and worker.
2. Open `http://localhost:3000` → click **"Agent Review"** in the top bar (or go to `/agents`).
3. Start a run from the command line: `aeo agent "Acme" --domain acme.com --topic ctem`
   (or via `POST /api/agent/run`).
4. The worker drives it research → plan → build → critic → **staged**.
5. Open it in the Review Queue, read the drafts and Critic verdicts, and **Approve** or **Reject**.

---

## 7. Deliberately left for later (future work, not gaps)

- The conversational "ATLAS" companion that narrates the agents' work in real time.
- A "publish to your CMS" agent (WordPress/Shopify) with one-click rollback — today a human ships
  the approved kit.
- The "cited by AI" reward axis (the top rung of the maturity ladder), which needs the citation
  data wired in.

These were scoped out on purpose so each plan shipped one complete, testable piece.
