# AEO Studio — Retention Foundation: what we're building and how it answers Arun's teardown

**Date:** 2026-06-16 · **Branch:** `feat/aeo-retention-foundation` · **Status:** built, build- & test-verified; adversarial review pending (see end).

---

## TL;DR

Arun's teardown had two halves. The **intake/friction** half was largely fixed in the
earlier redesign (URL-first, prefill-from-crawl, comprehensive-by-default, the
"who's building?" question removed). The **output/retention** half — *"the 30-day zip
causes procrastination, users tool-hop, nothing brings them back, DAU is zero"* — was the
bigger unsolved problem.

This work (Spec #1 of a sequenced plan) builds the **foundation** that makes the
output/retention fix possible, plus one visible win:

1. A single **AEO Score** the user can see and aim to raise.
2. A **persisted, resumable plan** with its own link — so there is finally *an object to
   return to*, on any device.
3. A **"Today" tray + sequenced phases** — so the plan reads as "do these 3 things now,"
   not a 30-day wall.

It deliberately stops short of the re-crawl "proof" loop and proactive nudges — those are
the next two specs, and the honest reason is below.

---

## What Arun said (recap of the teardown)

**Output & retention problems**
- "30 days" → users **procrastinate** and defer indefinitely.
- **Tool-hopping**: users copy tasks into a separate to-do app, breaking the loop.
- **No reason to return** after getting the file; DAU is the metric and the model produces
  **zero return visits**.
- Fix direction: **gamify + sequence** — priority buckets (week-1 essentials vs later) not
  a flat dump; **quick wins** before day 30; **proactively re-crawl** to confirm progress;
  each recommendation shows **current state → action → how-to**.
- **Differentiator** vs. just using Claude/ChatGPT directly = the workflow, the sequencing,
  and the proprietary judgment baked in.

**UX & friction** (mostly already addressed before this work)
- URL first; derive everything from the crawl; comprehensive by default; drop needless
  questions. **Core principle: minimal input + surprising output = the wow that retains.**

---

## What we're building (Spec #1)

| Piece | What it is |
|------|------------|
| **B0 — Canonical AEO Score** | One number (0–100) computed from the site profile we already produce, used everywhere so the same site never shows two different scores. |
| **B1 — Persisted + resumable plan** | The interactive plan is saved on the server and gets its own link (`/plan/<id>`). Progress survives a cache-clear and a device switch. Returning to the site shows a "Welcome back — resume your plan" banner. |
| **B2 — "Today" tray + focused phases** | The plan opens on the next ≤3 quick wins ("do now"); later phases collapse behind a "Coming up… Show everything" line. Phase labels read "Do these now / This week / Once you're rolling." |
| **B4-lite — Score ring** | A gauge at the top of the results: your score today, plus a ghosted target showing where finishing the plan gets you. |

## How each piece works (plain version)

- **The score** is derived from things the audit already computes — how much of the
  ideal site structure exists, how much of the customer journey the site covers, how
  complete its key pages are, and how confidently we read the business. It refines as the
  deep audit learns more about the site.
- **The saved plan** mints an unguessable link the moment the plan is generated; the
  address bar becomes `/plan/<id>`. Every time the user checks a task off, the progress is
  written to the server (and mirrored locally as a backup). Open that link on a phone,
  laptop, or after weeks away, and the plan and progress are exactly as left.
- **The "Today" tray** pulls the next three unfinished quick wins to the top as one-tap
  rows; as each is done it slides out and the next fills in, so the user is never staring
  at the whole month.
- **The ring** turns the abstract audit into a single graspable number with a verdict
  ("Barely visible / On the radar / Recommended / Top answer").

---

## How this answers Arun, point by point

| Arun's point | What Spec #1 does | Status |
|---|---|---|
| **"30 days" → procrastination** | Replaced the flat 30-day dump with a "Today" tray + focused phases and momentum labels ("Do these now"), so nothing reads as a month-long countdown. | **Addressed** |
| **Quick wins before day 30** | The tray surfaces the 3 quick wins first, ahead of any big task. | **Addressed** |
| **Tool-hopping breaks the loop** | The plan is worked *in-app* and progress is now saved server-side — no copying tasks into a separate to-do app to keep state. | **Addressed** |
| **No object to return to / zero return visits / DAU** | The plan now has a server home + a resumable `/plan/<id>` link + a "resume your plan" banner on return. There is finally something to come back to, on any device. | **Foundation shipped** (the *proactive* nudge that pulls people back is Spec #3) |
| **Gamify + sequence** | Sequencing is done (tray + phases). The score ring is the first gamification primitive — a number to raise. | **Partial** (levels/celebrations + a score that *climbs as you work* are Spec #2) |
| **Current state → action → how-to per recommendation** | Preserved and surfaced on every task (these fields already existed in the plan). | **Kept** |
| **Proactively re-crawl to confirm progress** | Not in this spec. The score deliberately only moves on a re-audit, so we never fake "verified." | **Spec #2 (the moat)** |
| **Differentiator vs. using Claude directly** | The score is snapshotted per plan, seeding "your score over time" — something a stateless chatbot can't do. The full moat (a re-crawl that *proves* your fix shipped) is the next spec. | **Seeded; moat is Spec #2** |
| **Minimal input + surprising output (the wow)** | Results now lead with the score + a do-now board instead of a metrics dashboard. The instant "here's what we already see" wow on the first screen is a separate track (Approach A). | **Partial** |

---

## What we deliberately deferred (and why)

Being honest so the team isn't surprised:

- **The re-crawl "proof" loop (Spec #2 — the moat).** This is *the* differentiator Arun
  named, and the one feature every persona in our role-play teardown said they'd pay for.
  We did **not** rush it, because the backend's current change-detection is hash-only and
  cannot honestly claim "we verified *this specific fix* is live." A false "we don't see
  it" on real work destroys trust faster than having no verification at all. Spec #2 builds
  an honest verifier first. (The data plumbing for it — per-plan score snapshots, a `run_id`
  link — is already laid down in this spec.)
- **Proactive return nudges (Spec #3).** A weekly "your re-check is ready" email is the
  realistic scheduled return-trigger, but it needs email infrastructure we don't have yet.
- **Level-ups / a score that climbs as you check tasks (Spec #2).** Tied to the verifier
  so a rising number is always *earned*, never self-graded.

The sequencing is intentional: **return-loop foundation → verified moat → acquisition
flywheel.** This spec is step one.

---

## Why this is the right first step

Arun's metric is DAU, and DAU is impossible without an object to return to. Today progress
lives only in one browser's local storage — device-locked, lost on cache-clear, invisible
to the return-visit metric. This spec fixes that root cause and makes the plan a durable,
linkable, sequenced thing the later retention features (verified score, weekly nudge,
public scorecard) all build on. It also lands two changes the user *feels* immediately —
a score to raise and a non-overwhelming board — so it isn't invisible plumbing.

---

## Status & verification

- **Built** on branch `feat/aeo-retention-foundation` (not yet committed).
- **Python:** ruff clean; 18 offline tests pass (new `test_plan_state.py` + existing schema tests).
- **Web:** `next build` succeeds — typechecks + lints clean; the new `/plan/[id]` route is registered.
- **Adversarial code review: incomplete.** A 3-lens review (correctness / backend / security)
  ran, but 12 of 13 finding-verifications failed on an account session limit; the one that
  completed was a dismissed false alarm. **This review must be re-run before commit.**
- **Setup note:** the new `plan_states` table needs migration `0012` applied; without it the
  plan still works via the local-storage fallback, but persistence/resume won't.
