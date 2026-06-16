"use client";

// The resumable plan link (B1): /plan/<id>. Fetches the persisted plan state and renders
// it standalone, so a bookmarked or shared link works on any device with no wizard state.
// A real 404 is a friendly dead-end; a transient outage (5xx/network) is a retryable
// "temporarily unavailable" — never told the user their valid link "expired".

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { PlanStateResponse } from "@/lib/types";
import { TopBar, Footer } from "@/components/chrome";
import { ResumedPlanView } from "@/components/results";

type Status = "loading" | "ready" | "missing" | "unavailable";

export default function PlanPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [state, setState] = useState<PlanStateResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [attempt, setAttempt] = useState(0); // bump to retry after a transient failure

  // session_start / return_visit once on load
  useEffect(() => {
    api.trackVisit();
  }, []);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setStatus("loading");
    api.getPlanState(id).then(
      (s) => {
        if (alive) {
          setState(s);
          setStatus("ready");
        }
      },
      (err: unknown) => {
        if (!alive) return;
        const code = (err as { status?: number }).status;
        // 404 → the plan really is gone; anything else (503/500/network) is retryable
        setStatus(code === 404 ? "missing" : "unavailable");
      },
    );
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  return (
    <>
      <TopBar />
      {status === "loading" && (
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-5 py-24 text-ink-500">
          <span className="h-5 w-5 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          Loading your saved plan…
        </div>
      )}
      {status === "missing" && (
        <div className="mx-auto max-w-3xl px-5 py-24 text-center">
          <h1 className="text-2xl font-semibold">This plan link isn't available</h1>
          <p className="mt-2 text-ink-500">
            It may have expired or the address is incomplete. You can build a fresh plan in a couple of minutes.
          </p>
          <a href="/#studio" className="btn-accent mt-6 inline-flex">
            Start a new plan →
          </a>
        </div>
      )}
      {status === "unavailable" && (
        <div className="mx-auto max-w-3xl px-5 py-24 text-center">
          <h1 className="text-2xl font-semibold">Your plan is temporarily unavailable</h1>
          <p className="mt-2 text-ink-500">
            We couldn't reach the plan store just now — your plan is safe. Please try again in a moment.
          </p>
          <button onClick={() => setAttempt((a) => a + 1)} className="btn-accent mt-6 inline-flex">
            ↻ Try again
          </button>
        </div>
      )}
      {status === "ready" && state && <ResumedPlanView state={state} />}
      <Footer />
    </>
  );
}
