"use client";

// v5 / Phase 3 item 3.4 — a pack's fixes, rendered INSIDE "Your plan".
//
// Before this, clicking a pack opened a TicketBoard below the pack grid: a second to-do
// surface, with its own layout, its own progress and its own vocabulary, sitting under the
// one the user was already working. Two lists of work with no relationship to each other.
// Now a pack's fixes are bucketed into the same three phases as the plan (Quick Wins →
// Foundation → Growth & Scale) and rendered with the same card chrome, so there is one list
// with a selector on top.
//
// Two things this deliberately does NOT do:
//
//  * It does not mount a second tracker. `useQuestTracker` owns the AGENCY milestones and
//    the share link, and TrackerView's comment requires exactly one instance so progress,
//    verification and the share link can never disagree. Pack tickets are a different
//    server-side family (`pack:N` milestones, reached through /api/tickets/*), so this
//    component owns only its own ticket fetch and renders BESIDE the tracker, not instead
//    of it. The "Check my site now" button, the share link and the developer handoff all
//    remain the tracker's, shared by both.
//
//  * It does not give pack tasks the plan's 3-state segmented control. A ticket has a FOURTH
//    state — closed_pending_verify — and moves through ACTIONS, not a free status set:
//    closing one enqueues a forced re-crawl and only that crawl may mark it verified
//    (CH-15). A radio button labelled "Verified" would let a user assert a verification that
//    has not happened, which is the precise dishonesty that loop exists to prevent. So the
//    row offers "Mark as done" → "Verifying…" → "Verified", and reopening, all wired to the
//    real endpoints.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { packFixDomId, packPlanPhases, packPlanProgress, type PackPlanTask } from "@/lib/packPlan";
import { phaseDisplayBlurb, phaseDisplayTitle } from "@/lib/phases";
import type { PackPreview, SkillPriority, Ticket } from "@/lib/types";
import { PhaseCardShell, STATUS_META } from "../MilestoneDashboard";
import { TaskHowTo } from "../TaskHowTo";
import { Check } from "../ui/icons";

/** How often to re-read tickets while any of them is awaiting its verification crawl.
 *  Matches the standalone board's cadence so the two behave identically. */
const VERIFY_POLL_MS = 5000;

export function PackPlanSection({
  runId,
  packs,
  selectedPack,
  onSelectPack,
  onUnlock,
  shareUrl,
  focusFixId,
}: {
  runId: number;
  packs: PackPreview[];
  /** Which pack's fixes are showing. */
  selectedPack: number | null;
  onSelectPack: (packIndex: number) => void;
  /** Open the unlock dialog for a locked pack (never a raw 403). */
  onUnlock: (packIndex: number) => void;
  /** The tracker's share link, so a pack task's dev handoff matches the plan's. */
  shareUrl: string | null;
  /** Anchor of a fix jumped to from the Pages tab — flashed so the user can see
   *  WHICH row they landed on. State, not a class mutation: the row is React-rendered,
   *  so a className set by hand is wiped by the next re-render (item 3.5). */
  focusFixId?: string | null;
}) {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [priorities, setPriorities] = useState<SkillPriority[]>([]);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const active = packs.find((p) => p.pack_index === selectedPack) ?? null;

  const load = useCallback(async () => {
    if (selectedPack == null) return;
    try {
      const res = await api.getPackTickets(runId, selectedPack);
      setTickets(res.tickets);
      setLocked(false);
      setError(null);
    } catch (e) {
      // A locked pack is a 403 by design (the server never ships a locked pack's findings).
      // That is a product state, not a failure — render the unlock path, never an error.
      if ((e as { status?: number })?.status === 403) {
        setLocked(true);
        setTickets([]);
        setError(null);
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, selectedPack]);

  // Per-page priorities give the adapter its best bucketing signal (CH-06 impact). Entirely
  // best-effort: without them the adapter falls back to each ticket's baseline score, so a
  // failed or locked detail fetch changes the ordering, never the contents.
  useEffect(() => {
    let cancelled = false;
    if (selectedPack == null) return;
    setTickets(null);
    setPriorities([]);
    void load();
    api
      .getPackDetail(runId, selectedPack)
      .then((d) => {
        if (cancelled) return;
        setPriorities(d.pages.flatMap((p) => p.detail?.priorities ?? []));
      })
      .catch(() => {
        /* ordering falls back to baseline score */
      });
    return () => {
      cancelled = true;
    };
  }, [runId, selectedPack, load]);

  // Poll only while something is actually awaiting its crawl, and stop the moment nothing
  // is — an always-on interval on a results page is a needless request every 5s forever.
  const anyVerifying = tickets?.some((t) => t.status === "closed_pending_verify") ?? false;
  useEffect(() => {
    if (!anyVerifying) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = setInterval(() => void load(), VERIFY_POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [anyVerifying, load]);

  const act = async (taskKey: string, fn: () => Promise<unknown>) => {
    setBusyKey(taskKey);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKey(null);
    }
  };

  if (packs.length === 0) return null;

  const phases = packPlanPhases(tickets, priorities);
  const progress = packPlanProgress(phases);

  return (
    <div className="space-y-4">
      <PackSelector packs={packs} selected={selectedPack} onSelect={onSelectPack} />

      {selectedPack == null ? (
        <p className="rounded-xl border border-dashed border-ink/15 p-5 text-sm text-ink-500">
          Pick a pack above to bring its fixes into your plan. They&apos;ll be sorted into the
          same Quick Wins / Foundation / Growth &amp; Scale order as everything else.
        </p>
      ) : locked ? (
        // The gate, rendered as an offer rather than an error.
        <div className="rounded-xl border border-accent/25 bg-accent/[0.05] p-5">
          <h4 className="text-base font-semibold">
            {active?.title ?? `Pack ${selectedPack}`} is locked
          </h4>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-500">
            Unlock this pack to bring its page-by-page fixes into your plan, with the same
            how-tos and automatic verification as the rest of your steps.
          </p>
          <button type="button" onClick={() => onUnlock(selectedPack)} className="btn-primary mt-3 !py-2 text-[13px]">
            Unlock {active?.title ?? `Pack ${selectedPack}`}
          </button>
        </div>
      ) : tickets === null ? (
        <div className="flex items-center gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-5 text-sm text-ink-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          Loading this pack&apos;s fixes…
        </div>
      ) : phases.length === 0 ? (
        <p className="rounded-xl border border-dashed border-ink/15 p-5 text-sm text-ink-500">
          No page-level fixes in this pack — everything we scored here is already in good
          shape.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-ink-500">
            <span>
              {progress.total} fix{progress.total === 1 ? "" : "es"} from{" "}
              <span className="text-ink">{active?.title ?? `Pack ${selectedPack}`}</span>, sorted
              into your plan
            </span>
            <span className="font-mono text-xs">
              {progress.verified}/{progress.total} verified
              {progress.in_progress > 0 && ` · ${progress.in_progress} in progress`}
            </span>
          </div>
          {error && (
            <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
              That didn&apos;t work: {error}
            </p>
          )}
          {phases.map((phase, i) => {
            const verified = phase.tasks.filter((t) => t.status === "verified_completed").length;
            const meta =
              verified === phase.tasks.length
                ? STATUS_META.verified_completed
                : phase.tasks.some((t) => t.status !== "pending")
                  ? STATUS_META.in_progress
                  : STATUS_META.pending;
            return (
              <PhaseCardShell
                key={phase.key}
                index={i}
                statusLabel={meta.label}
                statusPill={meta.pill}
                title={phaseDisplayTitle(phase.key, phase.key)}
                blurb={phaseDisplayBlurb(phase.key, "")}
                countLabel={`${verified}/${phase.tasks.length} verified`}
              >
                {phase.tasks.map((t) => (
                  <PackTaskRow
                    key={t.task_key}
                    task={t}
                    focused={packFixDomId(t.skill, t.page_url) === focusFixId}
                    shareUrl={shareUrl}
                    busy={busyKey === t.task_key}
                    onClose={() => act(t.task_key, () => api.closeTicket(runId, t.task_key))}
                    onReopen={() => act(t.task_key, () => api.reopenTicket(runId, t.task_key))}
                    onRecheck={() => act(t.task_key, () => api.recheckTicket(runId, t.task_key))}
                  />
                ))}
              </PhaseCardShell>
            );
          })}
        </>
      )}
    </div>
  );
}

/** Which pack's fixes are in the plan, and how to switch. A locked pack stays selectable —
 *  choosing it shows the unlock offer, which is the only way a user discovers what is behind
 *  it. Hiding locked packs here would make the paywall invisible rather than clear. */
function PackSelector({
  packs,
  selected,
  onSelect,
}: {
  packs: PackPreview[];
  selected: number | null;
  onSelect: (packIndex: number) => void;
}) {
  return (
    <div className="card p-4">
      <span className="label-mono">Fixes showing from</span>
      <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Choose which pack's fixes to work">
        {packs.map((p) => {
          const active = p.pack_index === selected;
          return (
            <button
              key={p.pack_index}
              type="button"
              onClick={() => onSelect(p.pack_index)}
              aria-pressed={active}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
                active
                  ? "border-accent/40 bg-accent/10 font-medium text-ink"
                  : "border-ink/10 text-ink-300 hover:border-ink/20 hover:text-ink"
              }`}
            >
              {p.locked && <span aria-hidden>🔒</span>}
              {p.title}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function PackTaskRow({
  task,
  shareUrl,
  busy,
  focused,
  onClose,
  onReopen,
  onRecheck,
}: {
  task: PackPlanTask;
  shareUrl: string | null;
  busy: boolean;
  focused?: boolean;
  onClose: () => void;
  onReopen: () => void;
  onRecheck: () => void;
}) {
  const [open, setOpen] = useState(false);
  const verifying = task.ticketStatus === "closed_pending_verify";
  const done = task.ticketStatus === "verified_completed";
  const lift =
    task.baseline_score != null && task.current_score != null
      ? task.current_score - task.baseline_score
      : null;

  return (
    // The cross-link target for the Pages tab. Derived from the SAME helper on both
    // sides so the anchor cannot drift (item 3.5).
    <li
      id={packFixDomId(task.skill, task.page_url)}
      className={`px-4 py-3 transition-shadow ${focused ? "rounded-lg ring-2 ring-accent/60" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`font-medium ${done ? "text-ink-300 line-through" : "text-ink"}`}>{task.label}</span>
          <p className="mt-0.5 text-sm text-ink-500">{task.action_required}</p>
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
                {task.baseline_score} → {task.current_score} (+{lift})
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
          taskKey={task.task_key}
          label={task.label}
          actionRequired={task.action_required}
          howTo={task.how_to}
          shareUrl={shareUrl}
        />
      )}
    </li>
  );
}
