"use client";

// v5 CH-08/CH-15 — the ticket board for one pack. Each finding is a ticket: id, status,
// assignee, target date, and the before→after skill score. "Mark done" closes the ticket
// and triggers a re-crawl that proves the lift; the board polls until it verifies. Design
// system per CH-12: existing tokens (.card, .input, .btn-*, label-mono, ink/accent).

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Ticket, TicketStatus } from "@/lib/types";

const STATUS_LABEL: Record<TicketStatus, string> = {
  pending: "To do",
  in_progress: "In progress",
  closed_pending_verify: "Verifying…",
  verified_completed: "Verified",
};

const SKILL_LABEL: Record<string, string> = {
  messaging: "Messaging",
  conversion: "Conversion",
  discovery_visibility: "Discovery & Visibility",
  proof_trust: "Proof & Trust",
  structure_ux: "Structure & UX",
};

function Delta({ baseline, current }: { baseline: number | null; current: number | null }) {
  if (baseline == null) return <span className="label-mono !text-[10px] text-ink-300">not baselined</span>;
  if (current == null)
    return (
      <span className="label-mono !text-[10px] text-ink-300">
        baseline {baseline} · awaiting verify
      </span>
    );
  const delta = current - baseline;
  const sign = delta > 0 ? "+" : "";
  const tone = delta > 0 ? "text-accent" : delta < 0 ? "text-red-400" : "text-ink-300";
  return (
    <span className="font-mono text-[12px] text-ink-500">
      {baseline} → <span className="text-ink">{current}</span>{" "}
      <span className={tone}>({sign}{delta})</span>
    </span>
  );
}

function TicketCard({
  ticket,
  runId,
  onChange,
}: {
  ticket: Ticket;
  runId: number;
  onChange: (t: Ticket) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<{ ticket: Ticket }>) {
    setBusy(true);
    try {
      onChange((await fn()).ticket);
    } catch {
      /* best-effort; the board re-polls */
    } finally {
      setBusy(false);
    }
  }

  const verifying = ticket.status === "closed_pending_verify";
  const done = ticket.status === "verified_completed";
  // A recorded current below baseline means the re-crawl didn't prove the lift yet.
  const notProven =
    verifying && ticket.current_score != null && ticket.baseline_score != null &&
    ticket.current_score < ticket.baseline_score;

  return (
    <div className="card flex flex-col gap-2.5 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <span className="label-mono !text-[10px] text-ink-300">
          {SKILL_LABEL[ticket.skill ?? ""] ?? ticket.skill}
        </span>
        <span className="label-mono !text-[10px] text-ink-300">{STATUS_LABEL[ticket.status]}</span>
      </div>
      <p className="m-0 text-[13.5px] leading-[1.5] text-ink">{ticket.action_required || ticket.label}</p>
      {ticket.page_url && (
        <span className="truncate font-mono text-[11px] text-ink-300" title={ticket.page_url}>
          {ticket.page_url}
        </span>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          defaultValue={ticket.assignee ?? ""}
          placeholder="Assignee"
          aria-label="Assignee"
          className="input !py-1.5 !text-[12px] max-w-[130px]"
          onBlur={(e) => {
            const v = e.target.value.trim();
            if (v !== (ticket.assignee ?? "")) void act(() => api.setTicketFields(runId, { task_key: ticket.task_key, assignee: v || null }));
          }}
        />
        <input
          type="date"
          defaultValue={ticket.target_date ?? ""}
          aria-label="Target date"
          className="input !py-1.5 !text-[12px] max-w-[140px]"
          onBlur={(e) => {
            const v = e.target.value || null;
            if (v !== (ticket.target_date ?? null)) void act(() => api.setTicketFields(runId, { task_key: ticket.task_key, target_date: v }));
          }}
        />
        <Delta baseline={ticket.baseline_score} current={ticket.current_score} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {(ticket.status === "pending" || ticket.status === "in_progress") && (
          <button type="button" disabled={busy} className="btn-primary !py-1.5 text-[12px]"
            onClick={() => void act(() => api.closeTicket(runId, ticket.task_key))}>
            Mark done
          </button>
        )}
        {verifying && (
          <>
            <span className="text-[12px] text-ink-300">{notProven ? "Not proven yet" : "Verifying the fix is live…"}</span>
            <button type="button" disabled={busy} className="btn-ghost !py-1.5 text-[12px]"
              onClick={() => void act(() => api.recheckTicket(runId, ticket.task_key))}>
              Recheck
            </button>
            <button type="button" disabled={busy} className="btn-ghost !py-1.5 text-[12px]"
              onClick={() => void act(() => api.reopenTicket(runId, ticket.task_key))}>
              Reopen
            </button>
          </>
        )}
        {done && <span className="text-[12px] text-accent">✓ Verified live</span>}
      </div>
    </div>
  );
}

export function TicketBoard({ runId, packIndex }: { runId: number; packIndex: number }) {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      setTickets((await api.getPackTickets(runId, packIndex)).tickets);
      setLocked(false);
    } catch (e) {
      // A locked pack is an expected state (v5 CH-02a), not a failure — say so rather than
      // showing the generic "couldn't load".
      if (typeof e === "object" && e !== null && (e as { status?: number }).status === 403) {
        setLocked(true);
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, packIndex]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while any ticket is verifying, so a proven lift flips to Verified without a reload.
  const anyVerifying = tickets?.some((t) => t.status === "closed_pending_verify") ?? false;
  useEffect(() => {
    if (!anyVerifying) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(load, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [anyVerifying, load]);

  function replace(t: Ticket) {
    setTickets((prev) => (prev ? prev.map((x) => (x.task_key === t.task_key ? t : x)) : prev));
  }

  if (locked)
    return (
      <p className="text-[13px] text-ink-300">
        Unlock this pack to see and work its fixes.
      </p>
    );
  if (error) return <p className="text-[13px] text-ink-300">Couldn&apos;t load tickets.</p>;
  if (tickets == null) return <p className="text-[13px] text-ink-300">Loading tickets…</p>;
  if (tickets.length === 0) return <p className="text-[13px] text-ink-300">No tickets for this pack yet.</p>;

  const verified = tickets.filter((t) => t.status === "verified_completed").length;
  return (
    <div className="flex flex-col gap-3">
      <p className="label-mono !text-[10px] text-ink-300">
        {verified} / {tickets.length} verified
      </p>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,300px),1fr))] gap-3">
        {tickets.map((t) => (
          <TicketCard key={t.task_key} ticket={t} runId={runId} onChange={replace} />
        ))}
      </div>
    </div>
  );
}
