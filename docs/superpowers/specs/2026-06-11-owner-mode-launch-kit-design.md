# Owner-mode launch kit — design

**Date:** 2026-06-11 · **Status:** approved (user, in-session)

## Problem

The launch kit (`build_asset_bundle`, SP-3) produces developer artifacts: `sitemap.xml`,
`navigation.md`, `content-briefs.md` (entity/intent jargon), `internal-linking.md`,
`schema-and-entities.md`, and per-page spec sheets ending in raw JSON-LD. The product's
target user is a non-technical business owner who usually has **no dev team** — for them
the kit is a folder of files they can't act on.

## Decision

The kit adapts to **who is building the website**. One new question in the UI
("Who's building your website?") maps to a `builder_mode` field:

| mode | audience | kit shape |
|---|---|---|
| `diy` (UI default) | owner using Wix/Squarespace/WordPress | plain-English action pack + paste-ready page drafts |
| `ai` | owner using ChatGPT / builder AI | one engineered prompt per page (no LLM calls server-side) |
| `hire` | owner hiring a freelancer | diy pack + job post + acceptance checklist |
| `dev` (API default) | has a developer | current bundle, **byte-identical** (backward compat) |

All owner modes (`diy`/`ai`/`hire`) share:

- `START-HERE.md` — replaces `README.md` at the root: what's in the folder + a 30-day
  week-by-week plan derived from the existing priority-ordered page list.
- `get-found-now.md` — visibility wins that need no website: Google Business Profile
  fields with suggested copy, directories for the business's category/location
  (small deterministic category→directories table in the packager), a review-ask script.
- `for-your-developer/` — the full current dev bundle moved into a subfolder (owners who
  later hire shouldn't have to regenerate). `STRATEGY.md` stays included when a profile
  is given.

Mode-specific:

- **diy:** `pages/<slug>.md` rewritten owner-facing — "Create a page called X", the full
  draft copy (reuses `draft_missing_page` exactly as today, same LLM behavior), and the
  JSON-LD demoted to an "optional technical extra" pointing at `platform-tips.md`
  (where to paste it in Wix / Squarespace / WordPress / GoDaddy).
- **ai:** `prompts/<slug>.md` instead of page drafts — deterministic templates embedding
  business name/category/location, the page's seed questions, and required entities;
  plus `prompts/how-to-use.md`. **No `draft_missing_page` calls → ai mode is fast even
  with the LLM on.**
- **hire:** diy assets + `hire-someone/job-post.md` (ready-to-post freelancer brief
  scoped from the page list) + `hire-someone/acceptance-checklist.md` (non-technical
  verification steps, e.g. paste a page into validator.schema.org).

## API & frontend

- `DeliverablesRequest.builder_mode: Literal["dev","diy","ai","hire"] = "dev"` on both
  `/api/deliverables` and `/api/deliverables.zip`.
- `build_asset_bundle(..., builder_mode="dev", business: dict | None = None)` — the
  endpoint passes `{name, category, location, services}` from the brief so
  `get-found-now.md` and the prompts can be concrete. `business=None` → generic copy.
- UI: step 5 gains four selection cards ("Me, with a website builder" default / "AI
  tools" / "I'll hire someone" / "My developer or agency"); the choice rides on the
  deliverables request. Kit file rows get friendly kind labels (`start_here` → "read
  this first", `prompt` → "AI prompt", `visibility` → "get found checklist", …).

## Non-goals

- No change to LLM usage or kit generation speed in diy/hire/dev modes.
- No server-side state; `builder_mode` is request-scoped.
- No new analysis — every asset is a transform of data the pipeline already computes.

## Phasing

1. mode plumbing + diy + hire (share ~90% of the work)
2. ai prompt pack
3. `get-found-now.md` (+ category→directories table)

## Testing

- Packager: asset-set assertions per mode; regression that `dev` output is unchanged;
  prompts contain seed questions + business name; ai mode performs zero draft calls.
- API: `builder_mode` passes through and changes the asset set.
