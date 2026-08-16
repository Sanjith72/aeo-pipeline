# User Scenarios — how the AEO pipeline serves real users

This maps the five required user scenarios to how the **shipped SP-1 intelligence layer**
handles each: the site is classified, routed to a scenario, and handed a deliverable +
prioritized action plan — instead of just a score. See
[`UPDATED_ARCHITECTURE.md`](UPDATED_ARCHITECTURE.md) for the engine details.

## Routing at a glance

| Scenario | `SiteClass` | `Scenario` enum | Deliverable | Dominant action categories |
|---|---|---|---|---|
| 1 · No website | `NONE` | `no_website` | AEO Website Blueprint | structure → content → journey |
| 2 · Single-page | `SINGLE_PAGE` | `single_page` | Restructuring Roadmap | structure → content → journey |
| 3 · Small site (2–10) | `SMALL` | `small_site` | Gap Analysis & Build Plan | content → journey → structure |
| 3b · Growing (11–50) | `MEDIUM` | `growing_site` | Gap Analysis & Authority Plan | content → authority → journey |
| 4 · Mature (51–200 / 200+) | `LARGE` / `ENTERPRISE` | `mature_site` | Consolidation & Authority Plan | authority → consolidation → linking |
| 5 · Agency | *(any tier)* | *(tier scenario)* + `agency_mode` | client-ready exec-summary framing | as per tier |

The **business model** (lead-gen / e-commerce / local / SaaS / agency / publisher /
enterprise) is detected in parallel and tunes *which* archetypes/pages are expected and
how the plan is framed. Agency is an overlay, not a separate tier (Scenario 5).

---

## Scenario 1 — User has no website

> *"I want to rank in AI search."* — provides business name, industry, location, services, competitors.

- **Who:** a business with no site (or pre-launch).
- **Problem:** zero discoverability; nothing for an answer engine to cite.
- **Has:** a business brief. **Missing:** an entire site.
- **Action:** classify `NONE` → `no_website`; generate the ideal-site blueprint (sitemap,
  page hierarchy, clusters, FAQ architecture, schema/entity strategy, internal-linking
  plan) with **no crawl required** — the blueprint generator + framework bootstrap already
  run crawl-free.
- **Deliverable:** **AEO Website Blueprint** the user hands to a developer/site builder.
- **SP-1 status:** the router + strategy for `no_website` exist today (an empty discovery
  routes here, producing the full missing-page plan). The dedicated *business-input entry
  point* (collecting name/industry/services without a domain) is **SP-2**.

## Scenario 2 — Single-page website ✅ worked example

- **Who:** a business whose whole site is one page.
- **Problem:** the old tool *just scored it badly.* Now it explains why and gives a plan.
- **Action:** classify `SINGLE_PAGE` → `single_page`; detect missing archetypes + all
  journey gaps; emit a **complete restructuring roadmap** (add About, Contact, Services,
  Industries, FAQ, Resources, Case Studies; then content; then journey coverage).
- **Deliverable:** **Restructuring Roadmap**.

**Live run** (`aeo profile example.com --no-llm`, a real 1-page site):

```
AEO PROFILE  example.com   (discovered 1 pages via recursive)
  Scenario      : single_page  ->  Restructuring Roadmap
  Site class    : single_page  (1 pages, structure 0%)
  Business model: lead_gen  (confidence 0.0, default)
  Single-page site: the biggest blocker to AI-search discoverability. Here is the expansion plan.
  Present pages : none
  Missing pages : about, contact, services, industries, faq, resources, case_studies
  Journey gaps  : awareness, consideration, decision, conversion, retention
  Coverage      : 6.7%  (14 missing ideal pages)
  ACTION PLAN:
     1. [structure    ] Add a about page  (low)
     …
     8. [content      ] Create: Attack Surface Management (ASM)  (medium)
     …
    16. [journey      ] Cover the awareness stage of the journey  (medium)
    …
    22. [authority    ] Build topical authority in the ctem cluster  (high)
```

This is the exact complaint from the review meeting, fixed: the single-pager now gets a
roadmap, not a bad grade. (The CTEM-themed content reflects the default framework; an
onboarded client gets topic-tailored pages — see SP-2.)

## Scenario 3 — Small website (5–10 pages)

- **Who:** a business with a foundation but gaps.
- **Action:** classify `SMALL` → `small_site`; the coverage diff surfaces missing content
  clusters / intents / entities; the journey engine finds missing stages; the router
  prioritizes **content and journey** fixes first (structure is mostly present).
- **Deliverable:** **Gap Analysis & Build Plan** (new-page recommendations + priority order).
- *(11–50 pages → `MEDIUM`/`growing_site`: same, with topical-authority/thin-cluster
  emphasis → **Gap Analysis & Authority Plan**.)*

## Scenario 4 — Mature website (100+ pages)

- **Who:** an established site with breadth.
- **Action:** classify `LARGE`/`ENTERPRISE` → `mature_site`; the wins shift from *adding*
  pages to **consolidation** (overlap/cannibalization audit of the largest content types),
  **internal linking** (pillar→supporting authority flow), and **deepening thin clusters**.
- **Deliverable:** **Consolidation & Authority Plan**.

## Scenario 5 — Agency user

- **Who:** an agency improving a client's site.
- **Action:** any tier scenario runs, but `BusinessModel.AGENCY` (or an explicit flag)
  sets `agency_mode`, which reframes the `narrative` as a **client-ready executive
  summary** and marks the deliverable client-facing — no separate code path.
- **Deliverable:** the tier's deliverable, packaged client-ready (executive summary +
  prioritized roadmap). Full polished export (branded PDF / bundle) is **SP-3**.

---

## The six questions, answered by the system

For every scenario the meeting asked six questions; here is where each is answered in code:

| Question | Answered by |
|---|---|
| Who is the user? | `business_intent.py` → `BusinessModel` (+ industry hints) |
| What problem are they solving? | `scenario.py` → `Scenario` + `headline`/`narrative` |
| What do they have? | `classification.py` → `StructureProfile.present_archetypes`, coverage `matched` |
| What are they missing? | `classification` missing archetypes + `coverage_diff` missing pages + `journey` gaps |
| What action should the app take? | `scenario.py` → prioritized `StrategyAction[]` |
| What is the final deliverable? | `scenario.py` → `deliverable` (+ SP-3 packaging) |
