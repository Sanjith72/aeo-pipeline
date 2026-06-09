# AEO Studio — Design System

**Direction:** *Blueprint / Precision Instrument* — a technical-editorial aesthetic. Near-monochrome
**graphite-on-paper** with one confident **cobalt** signal, expressive display type, a "measurement"
monospace voice, a faint **engineering-blueprint grid**, hairline borders, and sparse, high-impact
motion. It reads premium and trustworthy (Linear/Vercel register) but is recognizable on its own:
*if you removed the logo, the blueprint grid + monospace technical readouts would still identify it* —
fitting for a tool that generates blueprints.

## Tokens (`tailwind.config.ts` + `globals.css`)

**Type** — three intentional roles (no Inter/Roboto/system defaults):
- `--font-display` **Space Grotesk** — headings, wordmark (geometric, technical).
- `--font-sans` **IBM Plex Sans** — body (professional, distinctive).
- `--font-mono` **IBM Plex Mono** — the "measurement" voice: step numbers, scenario badges, slugs,
  metadata, micro-labels (`.label-mono`).

**Color** — dominant + accent + neutral, not evenly balanced:
- `ink` (near-black `#0b0f1a` → tints) — dominant; primary CTAs are ink (premium black).
- `paper` (warm off-white `#f6f5f2`) — neutral ground.
- `accent` (cobalt `#2b4cf0`) — used sparingly: links, active state, signal CTAs, the hero word.

**Depth** — two narrative shadows only: `shadow-card` (resting) and `shadow-lift` (hover/elevated).
Radii standardize on `rounded-xl`/`rounded-xl2`.

**Motion** — one entrance (`animate-fade-up` / `-slow` on the hero + workspace), hover elevation on
cards/actions/buttons, a pulse on the running audit. All CSS-only; fully disabled under
`prefers-reduced-motion`.

## Components (utility classes in `globals.css`)
`.card`, `.input`, `.field-label`, `.label-mono`, `.btn` + `.btn-primary` (ink) / `.btn-accent`
(cobalt) / `.btn-ghost` (hairline), `.blueprint-grid` (+ `-fade` mask). No inline `<style>` blocks,
no dead styles.

## Layout
Sticky translucent top bar → **hero** (blueprint grid, balanced headline, value prop, stat strip) →
**workspace** (9-step wizard: sticky stepper rail that becomes a horizontal scroller on mobile +
refined step cards) → **trust band** (three honest credibility cards — deterministic / standards /
private; no fabricated logos or testimonials) → footer.

## Accessibility
High-contrast ink-on-paper; one consistent `:focus-visible` ring; semantic `h1→h3` order;
labels tied to inputs; ≥40px touch targets; `prefers-reduced-motion` honored.

## Differentiation callout
Avoids generic SaaS by committing to **monochrome + monospace precision and a literal blueprint grid**
instead of purple-on-white gradients, balanced palettes, and Inter.

## Extending
Drop real client logos / testimonials into `TrustBand` when available (the structure is ready; we did
not fabricate social proof). New surfaces should reuse the tokens + component classes above.
