// web/app/studio/page.tsx — the product route. Marketing lives on `/`; everything
// stateful (wizard, crawls, audits, results) is the client-side StudioApp.
import { Footer, TopBar } from "@/components/chrome";
import { StudioApp } from "@/components/StudioApp";

export const metadata = { title: "Studio · AEO Studio" };

export default function StudioPage() {
  return (
    <>
      <TopBar />
      {/* Same film-grain overlay the studio section had on the single-page layout.
          overflow-x-CLIP (never hidden — that would create a scroll container and
          kill the sticky stepper) fences any wide decorative glow. */}
      <div className="grain relative overflow-x-clip">
        <StudioApp />
        <Footer />
      </div>
    </>
  );
}
