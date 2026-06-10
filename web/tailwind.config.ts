import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        // Dark theme: luminous ink on deep-navy night paper — the blueprint glows.
        // Token ROLES are unchanged (ink = text, paper = surfaces), so every
        // existing class keeps working; only the values inverted.
        ink: { DEFAULT: "#eef1f7", 700: "#c8cfdd", 500: "#98a2b8", 300: "#5d6781", 100: "#2a3349" },
        paper: { DEFAULT: "#0a0e17", 100: "#111727", 200: "#0d1120", 300: "#1a2236" },
        // One confident signal — brightened for dark backgrounds; 600 is the
        // HOVER step, so on dark it goes lighter, not darker.
        accent: { DEFAULT: "#5b78ff", 600: "#7d93ff", 50: "#19234a" },
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.5), 0 10px 28px -16px rgba(0,0,0,0.65)",
        lift: "0 2px 8px rgba(0,0,0,0.5), 0 24px 56px -20px rgba(0,0,0,0.75)",
        // accent halo for primary CTAs — on dark it reads as light, not shadow
        glow: "0 1px 2px rgba(0,0,0,0.4), 0 10px 36px -8px rgba(91,120,255,0.55)",
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
      },
      animation: {
        "fade-up": "fade-up 0.6s cubic-bezier(0.16,1,0.3,1) both",
        "fade-up-slow": "fade-up 0.8s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.18s cubic-bezier(0.16,1,0.3,1) both",
        pop: "pop 0.25s cubic-bezier(0.34,1.56,0.64,1) both",
        shimmer: "shimmer 1.6s linear infinite",
        "float-y": "float-y 7s ease-in-out infinite",
        "float-y-slow": "float-y 11s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
