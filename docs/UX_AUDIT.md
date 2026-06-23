# UX Audit — first-time user walkthrough (LLM-first lens)

**Date:** 2026-06-17. Grounded in `web/app/page.tsx` (the wizard), `web/components/results.tsx`,
`web/components/chrome.tsx`. Lens: *the LLM should decide; the user should validate, not configure.*
For every manual input we ask: can the LLM infer / prefill / recommend it, or progressively reveal?

## Current flow (what a first-timer actually does)

1. **Landing** — hero + "how it works" + FAQ chrome. CTA scrolls to the studio.
2. **Step 0 — "Your website"**: a *"Do you have a website? Yes / Not yet"* toggle **and** a URL field.
   Next → a fast crawl/profile runs (seconds) to prefill the next steps.
3. **Step 1 — "About you"**: business **name** (required), **industry** (combobox, prefilled),
   **location** (combobox, optional), **services** (free text, optional).
4. **Step 2 — "Competitors"**: auto-discovered list to tick + manual add.
5. **Step 3 — "Your goals"**: multi-select goals, optional free-text "what's frustrating you",
   an "personalize with AI" toggle. **"Create my plan"**.
6. **Analysis wait** — the deep audit runs **5–15 min** (local model), showing a stage checklist.
7. **A second build** — the interactive plan ("launch kit") builds (auto now; previously a 2nd click).
8. **Results** — score ring + tabs (Overview / Website plan / Strategy / Your plan) + Today tray +
   phases + "Verified live" + re-check bar.
   There's also a **"Skip — just analyze my site"** shortcut (steps ≥1) and a **resume banner**.

## Inventory

### Manual inputs — and the AI-first alternative
| Input | Today | Can the LLM infer it? | AI-first move |
|---|---|---|---|
| Website URL | typed (required) | n/a — the one true input | Keep. It's the seed. |
| "Have a website?" toggle | explicit Yes/Not yet | **Yes** — entering a URL answers it | **Remove the toggle**; infer from the URL, with a quiet "I don't have a site yet" link |
| Business name | prefilled from domain, editable | **Yes** (already `deriveName`) — and better from the crawl's `<title>`/org | Keep prefilled; upgrade source to crawl title |
| Industry | prefilled (now fixed — was "PEV") | **Yes** — derived from the crawl | Keep prefilled; show confidence + 1-tap correct |
| Location | combobox, optional | **Partly** — only when the site encodes one | Prefill when found; hide when not (don't ask) |
| **Services** | **free text, optional** | **Yes** — extractable from the site's pages | **Stop asking**; LLM extracts services from the crawl, user validates chips |
| Competitors | auto-discovered + manual | **Yes** (already discovered) | Keep; default-select the top N, collapse "add your own" |
| **Goals** | **multi-select (asked cold)** | **Yes** — recommendable from industry + site stage | **Recommend** 1–2 likely goals pre-ticked ("most {industry} sites start here"), user adjusts |
| "What's frustrating you" | free text, optional | **Yes** — the gap analysis already knows | Pre-frame from detected gaps; let the user edit, don't start blank |
| "Personalize with AI" toggle | explicit, default on | — | Remove as a *question*; make it the default behavior, surface as a subtle "rewriting in your voice" state |

### Decisions forced on the user
Have-website (removable), which goals (recommendable), which competitors (defaultable),
AI on/off (removable), skip vs. continue, which tab to open. **Most are removable or
convertible to a recommendation the user merely confirms.**

### Waiting periods
- Step-0 fast profile: seconds (acceptable; but currently hidden behind a spinner).
- **Deep audit: 5–15 min** — the dominant pain. Today: a static stage checklist.
- Plan build: now auto-fired (was a 2nd wait).

### Confusing navigation points
- Results **tabs jump** between unrelated views (Overview / plan / Strategy / kit) with no sense of
  "where am I / what's next" → Task 6 (guided Discover→…→Track).
- Two long waits in a row historically (audit, then plan build) — partly fixed.
- The score "only moves on re-audit" is correct but not explained on screen.

## Pain points, ranked (impact × how-AI-first-the-fix-is)

1. **The 5–15 min audit shows a static checklist** — peak abandonment. *Fix:* premium/animated
   loading + **incremental results** (homepage first, stream findings) — Task 3 / Approach A. **(High)**
2. **Step 0 asks two things** (toggle + URL) on the make-or-break screen. *Fix:* one URL field. **(High, S)**
3. **Goals asked cold** — decision fatigue. *Fix:* LLM-recommended pre-ticked goals. **(High, M)**
4. **Services asked as free text** the crawl could fill. *Fix:* LLM-extract + validate chips. **(Med, M)**
5. **Flat 30+ task list / tabs jump** — cognitive load + disorientation. *Fix:* priority folders +
   progressive disclosure (Task 1) and guided nav (Task 6). **(High, M/L)**
6. **No on-screen "what's next / value so far"** — the guided-workflow gap (Task 6). **(Med, L)**

## North star
A first-timer should: paste a URL → watch real findings stream in within seconds → land on a
pre-filled, mostly-correct picture they *confirm* (industry, services, goals, competitors all
inferred) → get a sequenced, priority-foldered plan → and only ever *validate*, never *configure*.
Today they fill ~6 fields and wait ~15 minutes at a spinner; the gap between those is this phase's work.
