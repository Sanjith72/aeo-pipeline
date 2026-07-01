import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
        // The editorial accent — one italic word per section, never body copy.
        serif: ["var(--font-serif)", "Georgia", "serif"],
      },
      colors: {
        // Black & white: neutral off-white ink on true-black paper, with PURE WHITE
        // reserved as the single "accent" signal — the brightest tone, used sparingly
        // (CTAs, links, the hero word, the starfield bloom) so emphasis comes from
        // brightness + the serif italic, not hue. Token ROLES are unchanged (ink = text,
        // paper = surfaces, accent = signal), so every existing class keeps working.
        // ink-300 is the dimmest text allowed on any paper shade — kept ≥4.5:1 (WCAG AA).
        ink: { DEFAULT: "#e9e9ec", 700: "#c7c7cd", 500: "#9a9aa2", 300: "#86868e", 100: "#28282e" },
        paper: { DEFAULT: "#0a0a0c", 100: "#121214", 200: "#0e0e10", 300: "#1a1a1e" },
        // The signal — pure white (the brightest tone; body ink sits just below it so
        // accents pop). NOTE: white is light, so anything using `bg-accent` must carry
        // DARK text (text-paper), never white. See `.btn-accent` in globals.css.
        accent: { DEFAULT: "#ffffff", 600: "#ffffff", 50: "#1c1c22" },
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.55), 0 10px 28px -16px rgba(0,0,0,0.7)",
        lift: "0 2px 8px rgba(0,0,0,0.55), 0 24px 56px -20px rgba(0,0,0,0.8)",
        // soft white halo for primary CTAs — on black it reads as light, not shadow
        glow: "0 1px 2px rgba(0,0,0,0.4), 0 10px 36px -8px rgba(255,255,255,0.18)",
      },
      borderRadius: { xl2: "1.25rem" },
      letterSpacing: { measure: "0.18em" },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(18px)", filter: "blur(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)", filter: "blur(0)" },
        },
        "grid-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.97) translateY(-4px)" },
          "100%": { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        pop: {
          "0%": { transform: "scale(0.4)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-450px 0" },
          "100%": { backgroundPosition: "450px 0" },
        },
        "float-y": {
          "0%, 100%": { transform: "translateY(-6px)" },
          "50%": { transform: "translateY(8px)" },
        },
        // Gentle bob on the Horizon hero's SCROLL cue.
        "horizon-bob": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(5px)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.6s cubic-bezier(0.16,1,0.3,1) both",
        "fade-up-slow": "fade-up 0.8s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.18s cubic-bezier(0.16,1,0.3,1) both",
        // Opacity-only entrance for tab-panel swaps — no translate, so switching tabs
        // never nudges the page vertically (R2-6 nav rework).
        "fade-in": "grid-in 0.3s ease both",
        pop: "pop 0.25s cubic-bezier(0.34,1.56,0.64,1) both",
        shimmer: "shimmer 1.6s linear infinite",
        "float-y": "float-y 7s ease-in-out infinite",
        "float-y-slow": "float-y 11s ease-in-out infinite",
        "horizon-bob": "horizon-bob 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
