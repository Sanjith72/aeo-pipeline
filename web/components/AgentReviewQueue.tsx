// web/components/AgentReviewQueue.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { summarizeRun } from "../lib/agentRun";
import type { AgentRunDetail, AgentRunSummary, AgentStreamMessage } from "../lib/types";

const TERMINAL = new Set(["staged", "approved", "rejected", "failed", "cancelled"]);

export function AgentReviewQueue() {
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [selected, setSelected] = useState<AgentRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const { runs } = await api.listAgentRuns("staged");
      setRuns(runs);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const open = useCallback((id: string) => {
    esRef.current?.close();
    setSelected(null);
    void api.getAgentRun(id).then(setSelected).catch((e) => setError((e as Error).message));
    // Live updates while the run is still working; closes itself on the terminal frame.
    esRef.current = api.streamAgentRun(id, (msg: AgentStreamMessage) => {
      if (msg.type === "done" || msg.type === "status") {
        if (msg.type === "done" || (msg.type === "status" && TERMINAL.has(msg.status))) {
          void api.getAgentRun(id).then(setSelected).catch(() => {});
        }
      }
    });
  }, []);

  useEffect(() => () => esRef.current?.close(), []);

  const decide = useCallback(
    async (id: string, decision: "approve" | "reject") => {
      setBusy(true);
      setError(null);
      try {
        if (decision === "approve") await api.approveAgentRun(id);
        else await api.rejectAgentRun(id);
        setSelected(null);
        await refreshList();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [refreshList],
  );

  const summary = selected ? summarizeRun(selected) : null;

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[18rem_1fr]">
      <aside className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Review queue</h2>
          <button onClick={() => void refreshList()} className="text-xs text-neutral-400 hover:text-neutral-700">
            Refresh
          </button>
        </div>
        {runs.length === 0 && <p className="text-sm text-neutral-400">No staged runs.</p>}
        <ul className="space-y-1">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => open(r.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                  selected?.id === r.id ? "border-neutral-900 bg-neutral-50" : "border-neutral-200 hover:bg-neutral-50"
                }`}
              >
                <span className="font-medium">{r.domain ?? r.id}</span>
                <span className="ml-2 text-xs text-neutral-400">{r.status}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section>
        {error && <p className="mb-3 rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
        {!selected && <p className="text-sm text-neutral-400">Select a run to review.</p>}
        {selected && summary && (
          <div className="space-y-4">
            <header className="flex items-center justify-between">
              <div>
                <h1 className="text-lg font-semibold">{selected.result?.headline ?? selected.domain ?? selected.id}</h1>
                <p className="text-xs text-neutral-500">
                  {summary.draftedCount} drafted · {summary.flaggedCount} flagged · ${summary.costUsd.toFixed(3)} ·{" "}
                  {selected.status}
                  {selected.current_step ? ` (${selected.current_step})` : ""}
                </p>
              </div>
              {summary.isStaged && (
                <div className="flex gap-2">
                  <button
                    disabled={busy}
                    onClick={() => void decide(selected.id, "reject")}
                    className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void decide(selected.id, "approve")}
                    className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm text-white hover:bg-neutral-700 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
              )}
            </header>

            <ol className="flex flex-wrap gap-2 text-xs">
              {(selected.steps ?? []).map((s) => (
                <li key={s.seq} className="rounded-full border border-neutral-200 px-2 py-0.5">
                  {s.agent} · {s.status}
                  {s.cost_usd ? ` · $${s.cost_usd.toFixed(3)}` : ""}
                </li>
              ))}
            </ol>

            <ul className="space-y-3">
              {(selected.result?.tasks ?? [])
                .filter((t) => t.draft)
                .map((t) => (
                  <li key={t.id} className="rounded-xl border border-neutral-200 p-4">
                    <div className="flex items-center justify-between">
                      <h3 className="font-medium">{t.title}</h3>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ${
                          t.critic?.needs_review ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"
                        }`}
                      >
                        {t.critic?.needs_review ? "Needs review" : "Looks clean"}
                      </span>
                    </div>
                    {t.critic?.claims_flagged && (
                      <p className="mt-1 text-xs text-amber-700">Claims to verify: {t.critic.claims.join(", ")}</p>
                    )}
                    <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-3 text-xs text-neutral-700">
                      {t.draft?.body_markdown ?? ""}
                    </pre>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
