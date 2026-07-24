// The free URL-first overview (v5 CH-09): /overview?domain=<url>. The hero's URL field
// lands here; the page is a thin server shell — the client view reads the search param
// (hence the Suspense boundary useSearchParams requires) and calls POST /api/overview.

import { Suspense } from "react";
import type { Metadata } from "next";
import { Footer, TopBar } from "@/components/chrome";
import { OverviewView } from "@/components/OverviewView";

export const metadata: Metadata = {
  title: "Your free site overview — AEO Studio",
  description:
    "Paste your website and see how it scores on Messaging, Conversion, Discovery & Visibility, Proof & Trust, and Structure & UX — free, no signup.",
};

export default function OverviewPage() {
  return (
    <>
      <TopBar />
      <main className="grain relative overflow-x-clip">
        <Suspense fallback={null}>
          <OverviewView />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
