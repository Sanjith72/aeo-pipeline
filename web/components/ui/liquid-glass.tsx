"use client";

// Liquid-glass CTAs — the Apple-style treatment every call-to-action on the landing
// page shares (hero pair, wizard Back/Continue, the "+ Add" buttons). A pill built
// from three stacked `inset: 0` layers behind a z-raised label:
//   1. tint + fallback blur — plain backdrop-filter: blur(3px) plus a white tint
//      (primary 0.16, secondary 0.04)
//   2. distortion — backdrop-filter: url(#container-glass), the SVG displacement
//      filter below. Chrome/Edge only; Safari/Firefox simply keep layer 1's blur,
//      which is why BOTH layers always ship.
//   3. glass edge highlights — inset box-shadows only, pointer-events: none.
//      Primary appends a soft white outer glow.
// Selection controls (goal cards, stepper rows, FAQ rows) are NOT glass — pills only.

import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

/** The `#container-glass` displacement filter — defined ONCE per page (app/layout.tsx)
 *  so any glass layer can reference it. Hidden, zero-sized, aria-hidden. */
export function GlassFilter() {
  return (
    <svg aria-hidden="true" className="absolute h-0 w-0 overflow-hidden">
      <defs>
        <filter id="container-glass" x="0%" y="0%" width="100%" height="100%" colorInterpolationFilters="sRGB">
          <feTurbulence type="fractalNoise" baseFrequency="0.05 0.05" numOctaves="1" seed="1" result="turbulence" />
          <feGaussianBlur in="turbulence" stdDeviation="2" result="blurredNoise" />
          <feDisplacementMap
            in="SourceGraphic"
            in2="blurredNoise"
            scale="70"
            xChannelSelector="R"
            yChannelSelector="B"
            result="displaced"
          />
          <feGaussianBlur in="displaced" stdDeviation="4" result="finalBlur" />
          <feComposite in="finalBlur" in2="finalBlur" operator="over" />
        </filter>
      </defs>
    </svg>
  );
}

// The glass-edge highlight stack, verbatim from the design spec. Primary CTAs append
// a white outer glow so the main action reads a step brighter than the secondary.
const EDGE_HIGHLIGHT = [
  "0 0 8px rgba(0,0,0,0.03)",
  "0 2px 6px rgba(0,0,0,0.08)",
  "inset 3px 3px 0.5px -3.5px rgba(255,255,255,0.09)",
  "inset -3px -3px 0.5px -3.5px rgba(255,255,255,0.85)",
  "inset 1px 1px 1px -0.5px rgba(255,255,255,0.6)",
  "inset -1px -1px 1px -0.5px rgba(255,255,255,0.6)",
  "inset 0 0 6px 6px rgba(255,255,255,0.12)",
  "inset 0 0 2px 2px rgba(255,255,255,0.06)",
  "0 0 12px rgba(0,0,0,0.15)",
].join(", ");
const EDGE_HIGHLIGHT_PRIMARY = `${EDGE_HIGHLIGHT}, 0 12px 36px -10px rgba(255,255,255,0.28)`;

type Variant = "primary" | "secondary";

function GlassLayers({ variant }: { variant: Variant }) {
  return (
    <>
      <span
        aria-hidden
        className="absolute inset-0 -z-10 overflow-hidden rounded-full"
        style={{
          background: variant === "primary" ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.04)",
          backdropFilter: "blur(3px)",
          WebkitBackdropFilter: "blur(3px)",
        }}
      />
      <span
        aria-hidden
        className="absolute inset-0 -z-10 overflow-hidden rounded-full"
        style={{
          backdropFilter: "url(#container-glass)",
          WebkitBackdropFilter: "url(#container-glass)",
        }}
      />
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-full"
        style={{ boxShadow: variant === "primary" ? EDGE_HIGHLIGHT_PRIMARY : EDGE_HIGHLIGHT }}
      />
    </>
  );
}

// `isolate` gives the -z-10 layers a local stacking context to sit behind the label
// without escaping under the page background.
const ROOT =
  "relative isolate inline-flex items-center justify-center rounded-full text-[14.5px] " +
  "transition-[transform,color,opacity] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] " +
  "hover:scale-105 disabled:pointer-events-none disabled:opacity-35";
const BY_VARIANT: Record<Variant, string> = {
  primary: "font-semibold text-accent",
  secondary: "font-medium text-ink-700 hover:text-accent",
};

type BaseProps = { variant?: Variant; className?: string; children: ReactNode };
export type LiquidButtonProps = BaseProps &
  (
    | ({ href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "children">)
    | ({ href?: undefined } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">)
  );

/** A liquid-glass pill. Pass `href` for an anchor, otherwise renders a `<button>`.
 *  Size via className (padding); label color/weight comes from the variant. */
export function LiquidButton({ variant = "primary", className = "", children, ...rest }: LiquidButtonProps) {
  const rootClass = `${ROOT} ${BY_VARIANT[variant]} ${className}`;
  const body = (
    <>
      <GlassLayers variant={variant} />
      <span className="relative z-[1] inline-flex items-center gap-[9px] whitespace-nowrap">{children}</span>
    </>
  );
  if (rest.href !== undefined) {
    return (
      <a {...(rest as AnchorHTMLAttributes<HTMLAnchorElement>)} className={rootClass}>
        {body}
      </a>
    );
  }
  const buttonProps = rest as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button type="button" {...buttonProps} className={rootClass}>
      {body}
    </button>
  );
}
