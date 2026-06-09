import "./globals.css";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Mono, IBM_Plex_Sans, Space_Grotesk } from "next/font/google";

// Display: geometric + technical character (not Inter/Roboto). Body: IBM Plex Sans —
// professional and distinctive. Mono: the "measurement" voice for labels & readouts.
const display = Space_Grotesk({ subsets: ["latin"], variable: "--font-display", weight: ["500", "600", "700"], display: "swap" });
const sans = IBM_Plex_Sans({ subsets: ["latin"], variable: "--font-sans", weight: ["400", "500", "600"], display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], variable: "--font-mono", weight: ["400", "500"], display: "swap" });

export const metadata: Metadata = {
  title: "AEO Studio — AI-search blueprints, strategy & implementation",
  description:
    "Turn any business into an AI-search-ready website blueprint, prioritized strategy, and developer-ready implementation plan.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-paper font-sans text-ink antialiased selection:bg-accent/20">
        {children}
      </body>
    </html>
  );
}
