// AEO Studio — the marketing landing. Hero, how-it-works, trust band, and FAQ, all from
// components/chrome.tsx; the product itself lives on /studio (components/StudioApp.tsx).
// A server component: every stateful piece here is a client leaf inside chrome.tsx.

import { Faq, Footer, Hero, HowItWorks, ReportPreview, TopBar, TrustBand, WhatYouGet } from "@/components/chrome";
import { FAQ_ITEMS } from "@/lib/faq";

// Structured data: the product (SoftwareApplication) and the FAQ — generated from
// the same FAQ_ITEMS the visible accordion renders, so they can never drift apart.
// Rendered here rather than in the layout so the FAQ schema only ever appears on
// the page that actually shows the FAQ.
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

export default function Page() {
  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(APP_JSONLD) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(FAQ_JSONLD) }}
      />
      <TopBar />
      <Hero />

      {/* Everything below the hero shares one film-grain overlay (design: whole-page grain).
          A wrapper div rather than body so the grain never re-composites against the hero's
          animating WebGL canvas. overflow-x-CLIP (never hidden — that would create a scroll
          container and kill the sticky FAQ rail) fences the 120%-wide section glow. */}
      <div className="grain relative overflow-x-clip">
        <ReportPreview />
        <WhatYouGet />
        <HowItWorks />
        <TrustBand />
        <Faq />
        <Footer />
      </div>
    </>
  );
}
