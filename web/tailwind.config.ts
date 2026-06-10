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
        // Near-monochrome ink on warm paper — an editorial, premium base.
        ink: { DEFAULT: "#0b0f1a", 700: "#202938", 500: "#46505f", 300: "#7b8595", 100: "#dfe2e7" },
        paper: { DEFAULT: "#f6f5f2", 100: "#fffffe", 200: "#eceae4", 300: "#dedbd2" },
        // One confident signal — used sparingly (links, active, accents).
        accent: { DEFAULT: "#2b4cf0", 600: "#1f39c7", 50: "#eef1fe" },
      },
      boxShadow: {
        card: "0 1px 2px rgba(11,15,26,0.04), 0 10px 28px -16px rgba(11,15,26,0.14)",
        lift: "0 2px 6px rgba(11,15,26,0.06), 0 22px 48px -20px rgba(11,15,26,0.26)",
      },
      borderRadius: { xl2: "1.25rem" },
      letterSpacing: { measure: "0.18em" },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
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
      },
      animation: {
        "fade-up": "fade-up 0.6s cubic-bezier(0.16,1,0.3,1) both",
        "fade-up-slow": "fade-up 0.8s cubic-bezier(0.16,1,0.3,1) both",
        "scale-in": "scale-in 0.18s cubic-bezier(0.16,1,0.3,1) both",
        pop: "pop 0.25s cubic-bezier(0.34,1.56,0.64,1) both",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
