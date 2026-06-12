import "./globals.css";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Mono, IBM_Plex_Sans, Instrument_Serif, Space_Grotesk } from "next/font/google";
import { MotionProvider } from "@/components/motion/primitives";
import { FAQ_ITEMS } from "@/lib/faq";

// Display: geometric + technical character. Body: IBM Plex Sans — professional and
// distinctive. Mono: the "measurement" voice for labels & readouts. Serif italic:
// a single editorial accent reserved for the one word that matters per section.
const display = Space_Grotesk({ subsets: ["latin"], variable: "--font-display", weight: ["500", "600", "700"], display: "swap" });
const sans = IBM_Plex_Sans({ subsets: ["latin"], variable: "--font-sans", weight: ["400", "500", "600"], display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], variable: "--font-mono", weight: ["400", "500"], display: "swap" });
const serif = Instrument_Serif({ subsets: ["latin"], variable: "--font-serif", weight: "400", style: ["normal", "italic"], display: "swap" });

export const viewport: Viewport = {
  themeColor: "#0a0e17",
};

export const metadata: Metadata = {
  title: "AEO Studio — Be the business AI recommends",
  description:
    "When customers ask ChatGPT, Perplexity, or Google AI for a recommendation, make sure it's you. AEO Studio analyzes how answer engines see your business and builds a personalized, step-by-step plan in minutes.",
  keywords: [
    "answer engine optimization",
    "AEO",
    "AI search visibility",
    "AI SEO",
    "ChatGPT recommendations",
    "Perplexity optimization",
    "website audit",
  ],
  openGraph: {
    title: "AEO Studio — Be the business AI recommends",
    description:
      "Answer five questions, get a complete plan to become the business AI assistants recommend — website blueprint, prioritized actions, and a ready-to-use launch kit.",
    type: "website",
    siteName: "AEO Studio",
  },
  twitter: {
    card: "summary",
    title: "AEO Studio — Be the business AI recommends",
    description:
      "Answer five questions, get a complete plan to become the business AI assistants recommend.",
  },
};

// Structured data: the product (SoftwareApplication) and the FAQ — generated from
// the same FAQ_ITEMS the visible accordion renders, so they can never drift apart.
const APP_JSONLD = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: "AEO Studio",
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  description:
    "AEO Studio analyzes how AI answer engines see a business and generates a personalized website blueprint, prioritized action plan, and launch kit.",
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
};

const FAQ_JSONLD = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: FAQ_ITEMS.map(({ q, a }) => ({
    "@type": "Question",
    name: q,
    acceptedAnswer: { "@type": "Answer", text: a },
  })),
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} ${mono.variable} ${serif.variable}`}>
      <body className="min-h-screen bg-paper font-sans text-ink antialiased selection:bg-accent/20">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(APP_JSONLD) }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(FAQ_JSONLD) }}
        />
        <MotionProvider>{children}</MotionProvider>
      </body>
    </html>
  );
}
