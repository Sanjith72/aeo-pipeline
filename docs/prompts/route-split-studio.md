# Claude Code prompt — lightweight route split (`/` marketing, `/studio` app)

Paste everything below the line into Claude Code from the repo root.

---

Split the AEO Studio web app so that marketing lives on `/` and the product lives on `/studio`. This is a **route split inside the existing Next.js project** — same repo, same deploy, same `layout.tsx`. Do not create a second app or a second domain.

## Current state (verified — re-check before you trust it)

`web/app/page.tsx` is a single 1311-line `"use client"` component. Its JSX return (line ~624) is:

```
<TopBar/> <Hero/> <HowItWorks/>            ← marketing, stateless, from components/chrome.tsx
<section id="studio" ref={studioRef}> …the entire product… </section>
<TrustBand/> <Faq/> <Footer/>              ← marketing, stateless, from components/chrome.tsx
```

Everything inside `<section id="studio">` reads state and handlers declared at the top of `Page()` (crawl/prefill/audit jobs, competitor picks, goals, step index, `studioRef`). The marketing components take no props and hold no state.

Private helper components defined at the bottom of `page.tsx` — `ErrorNote`, `AnalysisSequence`, `Stepper`, `StepHeader`, `Field` — are all studio-only and move with it. The pure helpers `splitList`, `deriveName`, `formatAge`, `recommendedGoals` also move.

Note `DisplayH2` and `SheetTag` are imported from `chrome.tsx` but used *inside* the studio section (lines ~653, ~664), so `StudioApp` must keep importing them from `chrome.tsx`. They are shared, not marketing-only.

## What to build

1. **`web/components/StudioApp.tsx` (new).** `"use client"`. Move `Page()`'s entire body — all state, effects, handlers, the `<section id="studio">` JSX, and the five private helper components — into an exported `StudioApp` component. Drop the `id="studio"` and `studioRef` scroll machinery; it existed only to scroll down a long single page. Keep every import it actually needs (`results.tsx`, `CompetitorPicker`, `GamificationStrip`, `Combobox`, `LiquidButton`, motion primitives, `DisplayH2`, `SheetTag`).

2. **`web/app/studio/page.tsx` (new).** Renders `<TopBar/>`, `<StudioApp/>`, `<Footer/>`. Add a `metadata` export (title like `Studio · AEO Studio`). Match the pattern already used in `web/app/agents/page.tsx`.

3. **`web/app/page.tsx` (rewrite).** Marketing only: `<TopBar/> <Hero/> <HowItWorks/> <TrustBand/> <Faq/> <Footer/>`. Remove the `"use client"` directive if — and only if — nothing left in the tree needs it. Check `Hero` → `components/ui/horizon-hero.tsx` and the `Faq` accordion first; if either is a client component that's fine, it stays a client leaf under a server page.

4. **Repoint every `#studio` link to `/studio`.** There are seven, and they are not all the same shape:
   - `web/components/chrome.tsx:151` — `href="#studio"`
   - `web/components/chrome.tsx:302` — `href="#studio"`
   - `web/components/ui/horizon-hero.tsx:198` — `<LiquidButton href="#studio">`
   - `web/components/results.tsx:1857` — `href="/#studio"`
   - `web/components/results.tsx:1880` — `window.location.href = "/#studio"`
   - `web/app/plan/[id]/page.tsx:67` — `href="/#studio"`
   - `web/app/page.tsx:168` — the deep-link comment describing `/?domain=…&name=…&autobuild=1#studio`

   Prefer Next.js `<Link href="/studio">` over raw `<a>` for the internal navigations so they client-side route.

5. **`web/app/layout.tsx` — delete the hash-scroll guard script** (the inline `dangerouslySetInnerHTML` block around line ~83 that strips `location.hash` and sets `scrollRestoration = 'manual'`). It exists solely to stop `/#studio` jump-scrolling and becomes actively harmful once `/studio` is a route — it would strip legitimate hashes. Its long explanatory comment goes with it.

6. **Preserve the deep-link contract.** `page.tsx` currently reads `?domain=…&name=…&autobuild=1` from the URL, uses it, then strips the params. That behavior must survive on `/studio` — `/studio?domain=acme.com&autobuild=1` has to work exactly as `/?domain=acme.com&autobuild=1#studio` did. Anything that generates those links must now point at `/studio`.

## Constraints

- Keep `layout.tsx` shared. Fonts, `GlassFilter`, `MotionProvider` stay global. Do not duplicate them into either route.
- Leave `app/plan/[id]` and `app/share/[token]` alone. They are already their own routes.
- **JSON-LD:** `layout.tsx` injects `APP_JSONLD` (`SoftwareApplication`) and `FAQ_JSONLD` on *every* page. Move both out of the layout and into `app/page.tsx` so they render on the marketing page only — the FAQ schema must not appear on a route with no FAQ on it. `FAQ_JSONLD` is generated from `lib/faq.ts`'s `FAQ_ITEMS`; keep that single-source-of-truth link intact.
- The state and its JSX must move together in one commit or nothing compiles. Do not try to move the JSX first.
- No behavior changes, no redesign, no copy edits. Pure relocation.

## Order of work

Do step 1 and 2 first — stand up `/studio` as a working route while leaving `app/page.tsx` completely untouched. Stop there and show me the diff. Both routes will render the studio for a moment; that's expected and fine. Once I confirm `/studio` works, continue with steps 3–6.

## Verify before you call it done

- `cd web && npx tsc --noEmit` clean.
- `npm run build` clean, and confirm `/` is emitted as static (`○`) rather than dynamic in the route table.
- `npm run dev`, then by hand: `/` renders marketing with no studio section; every CTA lands on `/studio`; `/studio` runs a full audit end to end; `/studio?domain=example.com&autobuild=1` auto-starts; `/plan/<id>` and `/share/<token>` still load.
- Run the existing tests: `npm test` (there are `.test.ts` files for `agentRun`, `gamify`, `goals`, `phases`, `predictedLift`, `suggest`).
- Compare the client bundle size of `/` before and after. If it hasn't dropped substantially, the studio's JS is still being shipped to the marketing page and the split has failed its main purpose — say so rather than declaring success.
