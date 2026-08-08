"use client";

// One workable pack fix, shared by the two surfaces that render tickets — the Pages tab
// (under each page's scores) and "Your plan" (inside the phase cards). One definition so
// the row, its badges, and its verify affordances cannot drift between the tabs.
//
// Deliberately NOT given the plan's 3-state segmented control: a ticket has a FOURTH
// state, closed_pending_verify, and moves through ACTIONS rather than a free status set —
// closing one enqueues a forced re-crawl and only that crawl may mark it verified (CH-15).
// A radio button labelled "Verified" would let a user assert a verification that has not
// happened, which is the precise dishonesty that loop exists to prevent.

import { useState } from "react";

import { packFixDomId } from "@/lib/packPlan";
import type { PackPreview, Ticket } from "@/lib/types";
import { TaskHowTo } from "@/components/TaskHowTo";
import { Check } from "@/components/ui/icons";

export function PackFixRow({
  ticket,
  shareUrl,
  busy,
  onClose,
  onReopen,
  onRecheck,
  className = "px-1 py-3",
}: {
  ticket: Ticket;
  shareUrl: string | null;
  busy: boolean;
  onClose: () => void;
  onReopen: () => void;
  onRecheck: () => void;
  /** Row inset — the Pages accordion runs tight (default); the plan's phase cards px-4. */
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const verifying = ticket.status === "closed_pending_verify";
  const done = ticket.status === "verified_completed";
  const lift =
    ticket.baseline_score != null && ticket.current_score != null
      ? ticket.current_score - ticket.baseline_score
      : null;

  return (
    // The anchor both surfaces have always agreed on. Kept even though nothing links here
    // today: it is one line, it is unit-tested, and it keeps a stable deep-link target.
    <li id={packFixDomId(ticket.skill, ticket.page_url)} className={className}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`text-[13.5px] font-medium ${done ? "text-ink-300 line-through" : "text-ink"}`}>
            {ticket.label}
          </span>
          <p className="mt-0.5 text-[12.5px] leading-[1.5] text-ink-500">{ticket.action_required}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
              aria-expanded={open}
            >
              {open ? "Hide how-to" : "Show me how →"}
            </button>
            {verifying && (
              <span className="text-ink-300">
                We&apos;re re-checking this page now — it flips to Verified once we can see the
                change live.
              </span>
            )}
            {done && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-300">
                <Check width={10} height={10} /> auto-detected live
              </span>
            )}
            {done && lift != null && lift > 0 && (
              <span className="font-mono text-emerald-300">
                {ticket.baseline_score} → {ticket.current_score} (+{lift})
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {busy ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          ) : done || verifying ? (
            <>
              {verifying && (
                <button type="button" onClick={onRecheck} className="btn-ghost !py-1 text-[11px]">
                  Check again
                </button>
              )}
              <button type="button" onClick={onReopen} className="btn-ghost !py-1 text-[11px]">
                Reopen
              </button>
            </>
          ) : (
            <button type="button" onClick={onClose} className="btn-primary !py-1 text-[11px]">
              Mark as done
            </button>
          )}
        </div>
      </div>
      {open && (
        <TaskHowTo
          taskKey={ticket.task_key}
          label={ticket.label}
          actionRequired={ticket.action_required ?? ""}
          howTo={ticket.how_to ?? undefined}
          shareUrl={shareUrl}
        />
      )}
    </li>
  );
}

/** The pack pill row both surfaces use to switch which pack is being worked. Hidden by the
 *  callers on a single-pack run — one unswitchable chip is noise, not a control. */
export function PackSelector({
  label,
  packs,
  selectedPack,
  onSelectPack,
  ariaLabel,
}: {
  label: string;
  packs: PackPreview[];
  selectedPack: number | null;
  onSelectPack: (packIndex: number) => void;
  ariaLabel: string;
}) {
  return (
    <div className="card p-4">
      <span className="label-mono">{label}</span>
      <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label={ariaLabel}>
        {packs.map((p) => (
          <button
            key={p.pack_index}
            type="button"
            onClick={() => onSelectPack(p.pack_index)}
            aria-pressed={p.pack_index === selectedPack}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
              p.pack_index === selectedPack
                ? "border-accent/40 bg-accent/10 font-medium text-ink"
                : "border-ink/10 text-ink-300 hover:border-ink/20 hover:text-ink"
            }`}
          >
            {p.locked && <span aria-hidden>🔒</span>}
            {p.title}
          </button>
        ))}
      </div>
    </div>
  );
}
