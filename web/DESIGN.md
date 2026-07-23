# AEO Studio — Design System

**Direction:** *Celestial Blueprint* — a technical-editorial aesthetic in **black & white**. Neutral
off-white ink on **true-black paper** with **pure white** reserved as the lone signal, expressive
display type, a "measurement" monospace voice, a faint **engineering-blueprint grid**, hairline
borders, and sparse, high-impact motion. The hero adds a container-scoped **WebGL silver starfield**
(three.js + UnrealBloom) behind the headline — the "pop" moment — while the grid + monospace readouts
keep the brand recognizable: *if you removed the logo, the blueprint grid + monospace technical
readouts would still identify it.* Reads premium and timeless (Linear / Vercel monochrome register),
with emphasis carried by brightness + the serif italic rather than hue.

## Tokens (`tailwind.config.ts` + `globals.css`)

**Type** — three intentional roles (no Inter/Roboto/system defaults):
- `--font-display` **Space Grotesk** — headings, wordmark (geometric, technical).
- `--font-sans` **IBM Plex Sans** — body (professional, distinctive).
- `--font-mono` **IBM Plex Mono** — the "measurement" voice: step numbers, scenario badges, slugs,
  metadata, micro-labels (`.label-mono`).

**Color** — dominant + accent + neutral, not evenly balanced (dark theme; token ROLES unchanged):
- `ink` (off-white `#e9e9ec` → tints) — text. `ink-300` is the dimmest allowed; kept ≥4.5:1 on paper.
- `paper` (true black `#0a0a0c` → raised tints `100/200/300`) — surfaces / neutral ground.
- `accent` (pure white `#ffffff`) — the brightest tone, used sparingly: links, active state, signal
  CTAs, the hero word, the starfield bloom. **White is light** — anything on `bg-accent` carries DARK
  text (`text-paper`), never white. Semantic colors (emerald/rose/amber) are unchanged.

**Depth** — two narrative shadows only: `shadow-card` (resting) and `shadow-lift` (hover/elevated).
Radii standardize on `rounded-xl`/`rounded-xl2`.

**Motion** — one entrance (`animate-fade-up` / `-slow` on the hero + workspace), hover elevation on
cards/actions/buttons, a pulse on the running audit. All CSS-only; fully disabled under
`prefers-reduced-motion`.

## Components (utility classes in `globals.css`)
`.card`, `.input`, `.field-label`, `.label-mono`, `.chip`, `.btn` + `.btn-primary` (ink) /
`.btn-accent` (white signal CTA — carries dark text) / `.btn-ghost` (hairline), `.blueprint-grid`
(+ `-fade` mask), `.grain`, `.glass`, `.word-accent`, `.step-in`/`.panel-in`, `.skeleton`. Section
headers use the `SheetTag no="0N"` kicker + `DisplayH2` (rising-word display headline) from
`components/chrome.tsx`; motion via the `components/motion/primitives` set (`Reveal`/`Stagger`/`Item`;
import `m`, never raw `motion`). No inline `<style>` blocks, no dead styles.

## Layout
Route split: **`/`** = marketing (server component that owns the SoftwareApplication + FAQPage
JSON-LD) → sticky translucent top bar → **hero** (WebGL flythrough + single URL field, one CTA) →
**report preview** (static sample result) → **what you get** (the 5 skills) → **how it works**
(4 concrete steps) → **who it's for** (three audience-framed credibility cards; no fabricated logos
or testimonials) → **FAQ** → footer. **`/overview`** = the free URL-first 5-skill overview.
**`/studio`** = the product: a 4-step wizard → tabbed results (Overview + one consolidated "Your
plan" section with the journey/progress visual at the top). **`/plan/<id>`** = a resumable saved plan.

## Accessibility
High-contrast ink-on-paper; one consistent `:focus-visible` ring; semantic `h1→h3` order;
labels tied to inputs; ≥40px touch targets; `prefers-reduced-motion` honored.

## Differentiation callout
Avoids generic SaaS by committing to **monochrome + monospace precision and a literal blueprint grid**
instead of purple-on-white gradients, balanced palettes, and Inter.

## Extending
Drop real client logos / testimonials into `TrustBand` when available (the structure is ready; we did
not fabricate social proof). New surfaces should reuse the tokens + component classes above.
