# LLM-First Product Strategy — AEO Studio

**Date:** 2026-06-17.

## The principle

> The LLM decides wherever possible. The user **validates**, not configures.

For every field the UI asks for, the test is: *can we infer it, prefill it, recommend it,
or progressively reveal it instead of asking?* The product should feel like Notion AI /
Cursor / Duolingo — minimal input, surprising output, one next action — not an enterprise
form or a 15-step wizard. We optimize to reduce friction, cognitive load, decision
fatigue, and time-to-value.

## How the shipped work embodies it

| Change | Configure → Validate move |
|---|---|
| **URL-first intake** (prior) + honest copy (P6) | The URL is the only required input; everything else is derived from the crawl. |
| **Industry/location prefill** (Task 4 fix) | Inferred from the crawl and shown for confirmation; the internal topic code never leaks as an industry. |
| **Pre-selected goal** (P7) | The universal AEO goal is pre-ticked — the user adjusts a default, not a blank slate. |
| **Auto-built plan, fast by default** (P1) | No "build" decision or second wait; AI personalization is an opt-in, not a gate. |
| **Priority folders + per-task signals** (Tasks 1+2) | The system ranks tasks (band/impact/difficulty) and surfaces only High by default; the user works the recommendation. |
| **Early findings + premium loading** (2a) | Value (score, headline, gaps) appears in seconds, before the long audit finishes. |
| **Crawl freshness / use-existing** (2b) | The system decides "this is recent" and offers the cached review — the user doesn't pick crawl depth or staleness. |
| **Override capture** (Task 7) | Every time the user *does* override a suggestion, we log the (suggested → chosen) pair as an eval signal to make the model better over time. |

## The standing inference-vs-ask audit (see `UX_AUDIT.md`)

Manual inputs and their LLM-first target:
- **Have-website toggle** → infer from the URL (remove).
- **Business name / industry / location** → prefilled from the crawl (done; confirm-only).
- **Services** → still asked as free text → should be LLM-extracted from the site (open).
- **Goals** → pre-recommended from industry/site stage (partially done; fuller recommendation open).
- **"Personalize with AI" toggle** → default behavior, not a question (partially done; full removal + in-place upgrade is open).
- **`challenges` free-text** → currently collected but never sent: decide wire-or-delete.

## Operating rule for new work

Before adding any input or decision to the UI, justify it against the principle above. If
the LLM (or the deterministic engine) can produce a sensible default, ship the default and
let the user correct it — and capture the correction (`user_override`) as training signal.
