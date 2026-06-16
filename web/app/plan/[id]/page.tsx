"use client";

// The resumable plan link (B1): /plan/<id>. Fetches the persisted plan state and renders
// it standalone, so a bookmarked or shared link works on any device with no wizard state.
// A missing/expired id is a friendly dead-end, not an error page.

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { PlanStateResponse } from "@/lib/types";
import { TopBar, Footer } from "@/components/chrome";
import { ResumedPlanView } from "@/components/results";

type Status = "loading" | "ready" | "missing";

export default function PlanPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const [state, setState] = useState<PlanStateResponse | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    if (!id) return;
    let alive = true;
    api.trackVisit();
    api.getPlanState(id).then(
      (s) => {
        if (alive) {
          setState(s);
          setStatus("ready");
        }
      },
      () => {
        if (alive) setStatus("missing");
      },
    );
    return () => {
      alive = false;
    };
  }, [id]);

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
      {status === "ready" && state && <ResumedPlanView state={state} />}
      <Footer />
    </>
  );
}
