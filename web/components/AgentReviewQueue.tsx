// web/components/AgentReviewQueue.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

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

  // "Start a run" form state.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [form, setForm] = useState({ name: "", domain: "", topic: "" });
  const [starting, setStarting] = useState(false);
  const [startMsg, setStartMsg] = useState<string | null>(null);

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

  useEffect(
    () => () => {
      esRef.current?.close();
      if (pollRef.current) clearInterval(pollRef.current);
    },
    [],
  );

  const startRun = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const name = form.name.trim();
      if (!name) {
        setError("A business name is required to start a run.");
        return;
      }
      setStarting(true);
      setError(null);
      setStartMsg(null);
      try {
        const { run_id } = await api.startAgentRun({
          name,
          domain: form.domain.trim() || undefined,
          topic: form.topic.trim() || undefined,
          services: [],
          competitors: [],
          goals: [],
          use_llm: false,
        });
        setStartMsg(`Run ${run_id} started — the agents are working. It will appear below in a minute or two.`);
        setForm({ name: "", domain: "", topic: "" });
        // Poll the queue so the new run surfaces automatically once it stages (~1–2 min).
        if (pollRef.current) clearInterval(pollRef.current);
        let ticks = 0;
        pollRef.current = setInterval(() => {
          ticks += 1;
          void refreshList();
          if (ticks >= 24 && pollRef.current) clearInterval(pollRef.current);
        }, 5000);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setStarting(false);
      }
    },
    [form, refreshList],
  );

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
    <div className="space-y-6">
      {/* ── start a new run ── */}
      <form onSubmit={startRun} className="card p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="label-mono">Start a run</h2>
          {startMsg && <span className="text-[12px] text-emerald-300">{startMsg}</span>}
        </div>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
          <div className="field-group">
            <span className="field-label">Business name</span>
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Acme"
            />
          </div>
          <div className="field-group">
            <span className="field-label">Domain (optional)</span>
            <input
              className="input"
              value={form.domain}
              onChange={(e) => setForm({ ...form, domain: e.target.value })}
              placeholder="acme.com"
              inputMode="url"
            />
          </div>
          <div className="field-group">
            <span className="field-label">Topic (optional)</span>
            <input
              className="input"
              value={form.topic}
              onChange={(e) => setForm({ ...form, topic: e.target.value })}
              placeholder="ctem"
            />
          </div>
          <button type="submit" disabled={starting} className="btn-accent !px-5 text-[13px]">
            {starting ? "Starting…" : "Start run"}
          </button>
        </div>
        <p className="mt-2.5 text-xs text-ink-300">
          The agents research → plan → draft → critique, then stage the result here for your approval.
        </p>
      </form>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-[20rem_1fr]">
        {/* ── queue sidebar ── */}
        <aside className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="label-mono">Review queue</h2>
          <button
            onClick={() => void refreshList()}
            className="text-[12px] text-ink-300 transition-colors hover:text-ink"
          >
            Refresh
          </button>
        </div>

        {runs.length === 0 ? (
          <div className="card p-5 text-sm text-ink-300">
            No staged runs yet. Start one with{" "}
            <code className="rounded bg-ink/[0.06] px-1.5 py-0.5 font-mono text-[12px] text-ink-500">
              aeo agent
            </code>
            .
          </div>
        ) : (
          <ul className="space-y-2">
            {runs.map((r) => {
              const active = selected?.id === r.id;
              return (
                <li key={r.id}>
                  <button
                    onClick={() => open(r.id)}
                    className={`flex w-full items-center justify-between gap-3 rounded-xl border px-3.5 py-3 text-left transition-all duration-200 ease-out ${
                      active
                        ? "border-accent bg-accent-50 shadow-card"
                        : "border-ink/10 bg-paper-100 hover:-translate-y-0.5 hover:border-ink/25 hover:shadow-card"
                    }`}
                  >
                    <span className="truncate text-sm font-medium text-ink">{r.domain ?? r.id}</span>
                    <span className="shrink-0 rounded-full border border-ink/10 bg-paper-200/70 px-2 py-0.5 text-[11px] uppercase tracking-wide text-ink-300">
                      {r.status}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </aside>

      {/* ── detail ── */}
      <section>
        {error && (
          <p className="mb-4 rounded-xl border border-rose-500/25 bg-rose-500/[0.08] px-4 py-2.5 text-sm text-rose-300">
            {error}
          </p>
        )}

        {!selected ? (
          <div className="card grid min-h-[18rem] place-items-center p-10 text-center">
            <div className="max-w-xs space-y-1.5">
              <p className="text-sm font-medium text-ink">Select a run to review</p>
              <p className="text-xs text-ink-300">
                Each staged run shows its agent steps, the drafted pages, and the Critic&apos;s verdict.
                Nothing publishes until you approve it.
              </p>
            </div>
          </div>
        ) : (
          summary && (
            <div className="space-y-5">
              {/* header */}
              <header className="card flex flex-wrap items-start justify-between gap-4 p-5">
                <div className="min-w-0">
                  <h1 className="truncate text-lg font-semibold text-ink">
                    {selected.result?.headline ?? selected.domain ?? selected.id}
                  </h1>
                  <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-300">
                    <span>{summary.draftedCount} drafted</span>
                    <span className="text-ink/20">·</span>
                    <span className={summary.flaggedCount > 0 ? "text-amber-300" : ""}>
                      {summary.flaggedCount} flagged
                    </span>
                    <span className="text-ink/20">·</span>
                    <span className="font-mono">${summary.costUsd.toFixed(3)}</span>
                    <span className="text-ink/20">·</span>
                    <span className="uppercase tracking-wide">
                      {selected.status}
                      {selected.current_step ? ` · ${selected.current_step}` : ""}
                    </span>
                  </p>
                </div>
                {summary.isStaged && (
                  <div className="flex shrink-0 gap-2">
                    <button
                      disabled={busy}
                      onClick={() => void decide(selected.id, "reject")}
                      className="btn-ghost !px-4 !py-2 text-[13px]"
                    >
                      Reject
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void decide(selected.id, "approve")}
                      className="btn-accent !px-4 !py-2 text-[13px]"
                    >
                      Approve
                    </button>
                  </div>
                )}
              </header>

              {/* step trace */}
              <ol className="flex flex-wrap gap-2">
                {(selected.steps ?? []).map((s) => (
                  <li
                    key={s.seq}
                    className="inline-flex items-center gap-1.5 rounded-full border border-ink/10 bg-paper-100 px-2.5 py-1 text-[11px] text-ink-500"
                  >
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        s.status === "ok" ? "bg-emerald-400" : s.status === "failed" ? "bg-rose-400" : "bg-ink/30"
                      }`}
                    />
                    <span className="font-medium text-ink">{s.agent}</span>
                    {Number(s.cost_usd) > 0 ? (
                      <span className="font-mono text-ink-300">${Number(s.cost_usd).toFixed(3)}</span>
                    ) : null}
                  </li>
                ))}
              </ol>

              {/* drafted pages */}
              <ul className="space-y-3">
                {(selected.result?.tasks ?? [])
                  .filter((t) => t.draft)
                  .map((t) => {
                    const needs = t.critic?.needs_review;
                    return (
                      <li key={t.id} className="card p-5">
                        <div className="flex items-center justify-between gap-3">
                          <h3 className="truncate font-medium text-ink">{t.title}</h3>
                          <span
                            className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${
                              needs
                                ? "border-amber-500/30 bg-amber-500/[0.12] text-amber-200"
                                : "border-emerald-500/30 bg-emerald-500/[0.12] text-emerald-300"
                            }`}
                          >
                            {needs ? "Needs review" : "Looks clean"}
                          </span>
                        </div>
                        {t.critic?.claims_flagged && (
                          <p className="mt-2 text-xs text-amber-200/90">
                            <span className="font-medium">Claims to verify:</span> {t.critic.claims.join(", ")}
                          </p>
                        )}
                        <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-ink/10 bg-paper-200/50 p-3.5 font-mono text-[12px] leading-relaxed text-ink-500">
                          {t.draft?.body_markdown ?? ""}
                        </pre>
                      </li>
                    );
                  })}
              </ul>
            </div>
          )
        )}
      </section>
      </div>
    </div>
  );
}
