// web/app/agents/page.tsx
import { AgentReviewQueue } from "../../components/AgentReviewQueue";
import { MotionProvider } from "../../components/motion/primitives";

export const metadata = { title: "Agent Review Queue · AEO Studio" };

export default function AgentsPage() {
  return (
    <MotionProvider>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <AgentReviewQueue />
      </main>
    </MotionProvider>
  );
}
