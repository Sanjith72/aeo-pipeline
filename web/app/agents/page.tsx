// web/app/agents/page.tsx
import { AgentReviewQueue } from "../../components/AgentReviewQueue";

export const metadata = { title: "Agent Review Queue · AEO Studio" };

export default function AgentsPage() {
  return (
    <main className="mx-auto min-h-[85vh] max-w-6xl px-6 py-12 sm:py-16">
      <a href="/" className="text-[13px] text-ink-500 transition-colors hover:text-ink">
        ← Back to Studio
      </a>
      <header className="mb-8 mt-4">
        <p className="sheet-index">Agent workspace</p>
        <h1 className="mt-1.5 text-2xl font-semibold sm:text-3xl">Agent Review Queue</h1>
        <p className="mt-2.5 max-w-2xl text-sm leading-relaxed text-ink-500">
          AI agents draft new pages for your site and check their own work, then stage everything here
          for your sign-off. Open a run to read each draft and the Critic&apos;s verdict, then{" "}
          <span className="text-ink">Approve</span> to keep it or{" "}
          <span className="text-ink">Reject</span> to discard. Nothing is ever published until you
          approve it.
        </p>
      </header>
      <AgentReviewQueue />
    </main>
  );
}
