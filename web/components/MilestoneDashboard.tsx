"use client";

// The Implementation Dashboard — the "Final Plan" as persisted, trackable milestones,
// rendered as the tracked list at the heart of the Strategy tab. Progress lives on the
// server (per site) and advances two ways: the owner toggles a task, OR the weekly
// verification crawl detects the recommended artifact live on the site and auto-marks it
// "Verified ✓". The data itself is owned by the shared QuestTracker (one instance in
// TrackerView, read by this list and the Roadmap tab's Quest Map), so the two tabs never
// disagree.

import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import type { Milestone, MilestoneStatus, MilestoneTask, PackPreview, Ticket } from "@/lib/types";
import { manualCopyHint, selectField, useCopyAction } from "@/lib/copy";
import { phaseDisplayBlurb, phaseDisplayTitle } from "@/lib/phases";
import type { PhaseKey } from "@/lib/phases";
import { ticketsByPhase } from "@/lib/packPlan";
import { Check } from "./ui/icons";
import { TaskHowTo } from "./TaskHowTo";
import { PackFixRow, PackSelector } from "./PackFixRow";
import type { QuestTracker } from "./quest/useQuestTracker";
import type { PackTicketsState } from "./quest/usePackTickets";

/** Everything "Your plan" needs to fold the selected pack's fixes into its phase cards.
 *  The ticket state itself is ResultsView's single usePackTickets instance — the same one
 *  the Pages tab renders — so completing a fix on either surface updates both. */
export interface PackWork {
  packs: PackPreview[];
  selectedPack: number | null;
  onSelectPack: (packIndex: number) => void;
  onUnlock: (packIndex: number) => void;
  state: PackTicketsState;
}

export const STATUS_META: Record<MilestoneStatus, { label: string; pill: string; dot: string }> = {
  pending: {
    label: "Pending",
    pill: "bg-ink/[0.05] text-ink-500 ring-1 ring-ink/10",
    dot: "bg-ink/20",
  },
  in_progress: {
    label: "In progress",
    pill: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
    dot: "bg-amber-500",
  },
  verified_completed: {
    label: "Verified ✓",
    pill: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
    dot: "bg-emerald-500",
  },
};

const STATUS_ORDER: MilestoneStatus[] = ["pending", "in_progress", "verified_completed"];

export function MilestoneDashboard({ tracker, packWork }: { tracker: QuestTracker; packWork?: PackWork }) {
  const { dash, error, verifying, lastVerify, shareUrl, checkSite } = tracker;
  // Every branch here used to collapse into "nothing new is live yet" — including the ones
  // where we never actually read the site. Each distinct outcome now says what happened,
  // and only genuinely new work is congratulated.
  const verifyNote = ((): { text: string; tone: "good" | "warn" } | null => {
    if (!lastVerify) return null;
    const { newlyVerified, alreadyLive, siteReachable, siteBlocked, baselined, skipped } = lastVerify;
    if (skipped === "disabled")
      return { text: "Automatic verification is turned off for this site.", tone: "warn" };
    if (skipped === "nothing_pending")
      return { text: "Everything we can check automatically is already verified.", tone: "good" };
    if (!siteReachable)
      return {
        text: siteBlocked
          ? "We couldn't read your site — it's behind a bot filter that blocked our check. Your changes may well be live; we just can't confirm them automatically."
          : "We couldn't reach your site just now, so nothing could be confirmed. This is on our side, not yours — try again in a minute.",
        tone: "warn",
      };
    if (baselined)
      return {
        text: alreadyLive > 0
          ? `We've taken a snapshot of your site. ${alreadyLive} step${alreadyLive === 1 ? " was" : "s were"} already in place, so ${alreadyLive === 1 ? "it's" : "they're"} marked done — not counted as new work. From here, anything you publish gets verified as a real change.`
          : "We've taken a snapshot of your site. From here, anything you publish gets verified as a real change.",
        tone: "good",
      };
    if (newlyVerified > 0)
      return {
        text: `Nice — we found ${newlyVerified} change${newlyVerified === 1 ? "" : "s"} live and marked ${newlyVerified === 1 ? "it" : "them"} verified.`,
        tone: "good",
      };
    return {
      text: "We read your site, but none of the remaining steps are live yet. Publish a change, then check again.",
      tone: "good",
    };
  })();

  // Fire the list event before the shared write so telemetry keeps naming this surface.
  // The segmented control re-sends the current status on a same-state click — skip those.
  const setStatus = (task: MilestoneTask, status: MilestoneStatus) => {
    if (task.status === status) return;
    api.track("milestone_task_status", { task_key: task.task_key, status });
    void tracker.setStatus(task.task_key, status);
  };

  if (error && !dash) {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-500/[0.06] p-5 text-sm text-rose-200">
        Couldn&apos;t load your tracker: {error}
      </div>
    );
  }
  if (!dash) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-5 text-sm text-ink-500">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
        Setting up your tracker…
      </div>
    );
  }

  const { progress, milestones } = dash;
  const done = progress.pct === 100 && progress.total > 0;

  // The selected pack's fixes, bucketed into the SAME three phases the plan renders below —
  // one list, not a second stack that also says "Quick Wins" (the duplicate-heading problem
  // that got the old standalone section removed). packWork is absent on the no-domain path;
  // tickets are null while loading, which buckets to nothing and simply adds no rows yet.
  const packState = packWork?.state;
  const packPriorities = packState?.pages?.flatMap((p) => p.detail?.priorities ?? []) ?? [];
  const packPhases = packState && !packState.locked ? ticketsByPhase(packState.tickets, packPriorities) : [];
  const packByPhase = new Map(packPhases.map((p) => [p.key, p.tickets]));
  const packOnlyPhases = packPhases.filter((p) => !milestones.some((m) => m.milestone_key === p.key));
  const activePack = packWork?.packs.find((p) => p.pack_index === packWork.selectedPack) ?? null;
  const packTitle =
    activePack?.title ?? (packWork && packWork.selectedPack != null ? `Pack ${packWork.selectedPack}` : null);

  return (
    <div className="space-y-6">
      {/* headline progress + the automatic site check. The Developer handoff card is its
          own separate section (DeveloperHandoffPanel), composed by the Strategy tab. */}
      <div className="card p-5 sm:p-6">
        {/* A real heading so the tracked plan is reachable by heading navigation (the tab's
            other h3s are the extras and the developer handoff). */}
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="label-mono">Automatic site check</h3>
          <span className="font-mono text-xs text-ink-500">
            {progress.verified} / {progress.total} verified
          </span>
        </div>
        <div
          className="mb-4 h-2 overflow-hidden rounded-full bg-ink/[0.07]"
          role="progressbar"
          aria-valuenow={progress.pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Implementation progress"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
            style={{ width: `${progress.pct}%` }}
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-500">
            {progress.in_progress > 0 && (
              <>
                <span className="font-medium text-amber-200">{progress.in_progress} in progress</span> ·{" "}
              </>
            )}
            Our crawler re-checks your live site every week and marks a step done once it can
            confirm the new page is live — or check on demand. Steps without an on-site
            signal (and anything we can&apos;t confirm) stay yours to tick off.
          </p>
          <button
            type="button"
            onClick={checkSite}
            disabled={verifying}
            className="btn-primary !py-2 text-[13px]"
            title="Re-scan your live site now and verify any changes you've published"
          >
            {verifying ? (
              <>
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                Checking your site…
              </>
            ) : (
              "Check my site now"
            )}
          </button>
        </div>
        {/* The shared tracker error, rendered where it can actually be SEEN. The only other
            branch that reads `error` is the `error && !dash` early return above, which is
            unreachable once the dashboard has loaded — so every failed check, status toggle,
            and (worst) handoff-link revoke used to fail completely silently. */}
        {error && (
          <p
            role="alert"
            className="step-in mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300"
          >
            That didn&apos;t work: {error}
          </p>
        )}
        {verifyNote && !error && (
          <p
            className={`step-in mt-3 rounded-lg border px-3 py-2 text-sm ${
              verifyNote.tone === "warn"
                ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
            }`}
          >
            {verifyNote.text}
          </p>
        )}
        {done && (
          <p className="step-in mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            Every milestone is verified live — your business is set up to be the one AI recommends. 🎉
          </p>
        )}
      </div>

      {/* Which pack's fixes are folded into the phases below, and how to switch. Hidden on
          a single-pack run (one unswitchable chip is noise, and the auto-select in
          PagesPanel has already chosen it). */}
      {packWork && packWork.packs.length > 1 && (
        <PackSelector
          label="Fixes from"
          packs={packWork.packs}
          selectedPack={packWork.selectedPack}
          onSelectPack={packWork.onSelectPack}
          ariaLabel="Choose which pack's fixes are folded into your plan"
        />
      )}
      {packState?.lockedFixCount != null && packState.lockedFixCount > 0 && (
        <p className="text-[12.5px] text-ink-500">
          <span aria-hidden>🔒</span> {packState.lockedFixCount} more fix
          {packState.lockedFixCount === 1 ? "" : "es"} {packState.lockedFixCount === 1 ? "is" : "are"} in
          locked packs.
        </p>
      )}
      {/* A locked pack is an expected product state (CH-02a): an offer, never an error. */}
      {packWork && packState?.locked && packWork.selectedPack != null && (
        <div className="rounded-xl border border-accent/25 bg-accent/[0.05] p-5">
          <h4 className="text-base font-semibold">{packTitle} is locked</h4>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-500">
            Unlock this pack to work its page-by-page fixes right here, inside your plan.
          </p>
          <button
            type="button"
            onClick={() => packWork.onUnlock(packWork.selectedPack as number)}
            className="btn-primary mt-3 !py-2 text-[13px]"
          >
            Unlock {packTitle}
          </button>
        </div>
      )}
      {packState?.fixError && (
        <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          That didn&apos;t work: {packState.fixError}
        </p>
      )}

      {milestones.map((m, i) => (
        <MilestoneCard
          key={m.milestone_key}
          milestone={m}
          index={i}
          shareUrl={shareUrl}
          onSetStatus={setStatus}
          packTickets={packByPhase.get(m.milestone_key as PhaseKey) ?? []}
          packTitle={packTitle}
          packState={packState}
        />
      ))}
      {/* A phase the plan has no milestone for can still have pack fixes — they must land
          somewhere visible rather than being dropped for want of a card to sit in. */}
      {packOnlyPhases.map((p, i) => (
        <PackOnlyPhaseCard
          key={p.key}
          phaseKey={p.key}
          tickets={p.tickets}
          index={milestones.length + i}
          packTitle={packTitle}
          packState={packState as PackTicketsState}
          shareUrl={shareUrl}
        />
      ))}
    </div>
  );
}

// Developer handoff — its own clearly separate section on the Strategy tab (pulled out of
// the gamified roadmap). For teams with a dev team: the master read-only tracking link that
// gives a developer the whole plan and live progress, with one-click revoke+reissue (hard
// confirm — rotation kills the old link for anyone holding it). Each step's paste-ready
// technical brief lives in that step's "Send to Developer" expander above.
export function DeveloperHandoffPanel({ tracker }: { tracker: QuestTracker }) {
  const { shareUrl, rotating } = tracker;
  const inputRef = useRef<HTMLInputElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => shareUrl,
    selectFallback: () => selectField(inputRef.current),
    clearSelection: () => inputRef.current?.setSelectionRange(0, 0),
  });
  const onRotate = () => {
    if (
      !window.confirm(
        "This will permanently disable the current link for anyone who has it. Are you sure?",
      )
    )
      return;
    void tracker.rotateShareLink();
  };
  return (
    <div className="card border-accent/20 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <span className="label-mono !text-accent">For teams with a developer</span>
          <h3 className="mt-1 text-base font-semibold">Developer handoff</h3>
          <p className="mt-0.5 max-w-xl text-sm text-ink-500">
            One read-only link gives a developer your whole plan and live progress — no login. Share
            it freely; revoke it the moment it should stop working. Every step above also carries a
            paste-ready technical brief under “Send to Developer”.
          </p>
        </div>
        <button
          type="button"
          onClick={onRotate}
          disabled={rotating || !shareUrl}
          className="btn-ghost shrink-0 !py-1.5 text-[12px] text-rose-300 hover:!text-rose-200"
          title="Permanently disable the current link and create a new one"
        >
          {rotating ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-rose-300/40 border-t-rose-300" />
              Regenerating…
            </>
          ) : (
            "Revoke & generate new link"
          )}
        </button>
      </div>
      <div className="mt-3">
        <span className="label-mono">Current master share link</span>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            ref={inputRef}
            readOnly
            value={shareUrl ?? "Setting up your link…"}
            onFocus={(e) => e.currentTarget.select()}
            className="min-w-0 flex-1 rounded-md border border-ink/10 bg-paper px-2.5 py-1.5 font-mono text-[12px] text-ink-700"
            aria-label="Current developer share link"
          />
          <button type="button" onClick={copy} disabled={!shareUrl} className="btn-primary shrink-0 !py-1.5 text-[12px]">
            {phase === "copied" ? "Copied ✓" : phase === "manual" ? manualCopyHint() : "Copy link"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The phase-card chrome — header pill, phase title, verified count, blurb, then a divided
 * list of rows. Exported because Phase 3 item 3.4 folds a pack's fixes into "Your plan" and
 * they must look like the plan, not like a second product bolted underneath it. Both
 * surfaces render through this one definition so the two cannot drift apart visually.
 *
 * Rows are `children` rather than a task list: plan tasks and pack tickets carry different
 * state machines (a ticket has a 4th state, closed_pending_verify, and moves through
 * actions rather than a free status set), so they own their own row rendering while sharing
 * the frame around it.
 */
export function PhaseCardShell({
  statusLabel,
  statusPill,
  title,
  blurb,
  countLabel,
  index = 0,
  children,
}: {
  statusLabel: string;
  statusPill: string;
  title: string;
  blurb?: string | null;
  countLabel: string;
  index?: number;
  children: ReactNode;
}) {
  return (
    <div
      className="step-in overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100"
      style={{ animationDelay: `${Math.min(index, 4) * 70}ms` }}
    >
      <div className="border-b border-ink/[0.06] bg-paper-200/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${statusPill}`}>{statusLabel}</span>
          <h4 className="font-semibold">{title}</h4>
          <span className="font-mono text-xs text-ink-300">{countLabel}</span>
        </div>
        {blurb && <p className="mt-1 text-xs text-ink-300">{blurb}</p>}
      </div>
      <ul className="divide-y divide-ink/[0.06]">{children}</ul>
    </div>
  );
}

function MilestoneCard({
  milestone,
  index,
  shareUrl,
  onSetStatus,
  packTickets = [],
  packTitle,
  packState,
}: {
  milestone: Milestone;
  index: number;
  shareUrl: string | null;
  onSetStatus: (task: MilestoneTask, status: MilestoneStatus) => void;
  /** The selected pack's fixes belonging to THIS phase (item 3.4) — same card, same list. */
  packTickets?: Ticket[];
  packTitle?: string | null;
  packState?: PackTicketsState;
}) {
  const verified = milestone.tasks.filter((t) => t.status === "verified_completed").length;
  const packVerified = packTickets.filter((t) => t.status === "verified_completed").length;
  // The pill must describe what the card now CONTAINS: a milestone the server calls done
  // above unfinished pack fixes is not a done card.
  const status: MilestoneStatus =
    milestone.status === "verified_completed" && packVerified < packTickets.length
      ? "in_progress"
      : milestone.status;
  const meta = STATUS_META[status];
  // What is DONE folds away (the disclosure below); what is left to do stays in view. A
  // ticket awaiting its verification crawl is NOT done — its "Verifying…"/"Check again"
  // affordances are the CH-15 loop and must stay visible.
  const activeTasks = milestone.tasks.filter((t) => t.status !== "verified_completed");
  const doneTasks = milestone.tasks.filter((t) => t.status === "verified_completed");
  const activeTickets = packTickets.filter((t) => t.status !== "verified_completed");
  const doneTickets = packTickets.filter((t) => t.status === "verified_completed");
  return (
    <PhaseCardShell
      statusLabel={meta.label}
      statusPill={meta.pill}
      title={phaseDisplayTitle(milestone.milestone_key, milestone.title)}
      blurb={milestone.blurb}
      countLabel={`${verified + packVerified}/${milestone.tasks.length + packTickets.length} verified`}
      index={index}
    >
      {activeTasks.map((t) => (
        <TaskRow key={t.task_key} task={t} shareUrl={shareUrl} onSetStatus={onSetStatus} />
      ))}
      {activeTickets.length > 0 && packState && (
        <PackTicketRows tickets={activeTickets} packTitle={packTitle} packState={packState} shareUrl={shareUrl} />
      )}
      <DoneFold count={doneTasks.length + doneTickets.length}>
        {doneTasks.map((t) => (
          <TaskRow key={t.task_key} task={t} shareUrl={shareUrl} onSetStatus={onSetStatus} />
        ))}
        {packState &&
          doneTickets.map((t) => (
            <PackFixRow
              key={t.task_key}
              ticket={t}
              shareUrl={shareUrl}
              busy={packState.busyKey === t.task_key}
              onClose={() => packState.close(t.task_key)}
              onReopen={() => packState.reopen(t.task_key)}
              onRecheck={() => packState.recheck(t.task_key)}
              className="px-4 py-3"
            />
          ))}
      </DoneFold>
    </PhaseCardShell>
  );
}

/**
 * The collapsed home of everything already done — crawl-verified, already-in-place at the
 * baseline, or ticked off by the owner. The first thing a returning user sees must be what
 * is LEFT, not a wall of struck-through wins; but the wins stay one click away (with their
 * badges and baseline→current lifts intact) because "what's already in place" is the
 * evidence the plan is working. Progress numbers, coins and the share page all read the
 * UNFILTERED sets — this folds rows, it never uncounts them.
 */
function DoneFold({ count, children }: { count: number; children: ReactNode }) {
  if (count === 0) return null;
  return (
    <li className="px-4 py-2.5">
      <details className="group/done">
        <summary className="cursor-pointer list-none text-[13px] text-ink-300 transition-colors hover:text-accent">
          <span className="group-open/done:hidden">
            ✓ Already in place ({count}) — show what&apos;s done →
          </span>
          <span className="hidden group-open/done:inline">Hide what&apos;s done</span>
        </summary>
        <ul className="mt-2 divide-y divide-ink/[0.06] border-t border-ink/[0.06]">{children}</ul>
      </details>
    </li>
  );
}

/** The pack's rows inside a phase card: a quiet provenance line, then the same workable
 *  rows the Pages tab renders (PackFixRow — actions, not the 3-state radio; see its
 *  comment for why a "Verified" radio would be dishonest here). */
function PackTicketRows({
  tickets,
  packTitle,
  packState,
  shareUrl,
}: {
  tickets: Ticket[];
  packTitle?: string | null;
  packState: PackTicketsState;
  shareUrl: string | null;
}) {
  return (
    <>
      {packTitle && (
        <li className="bg-paper-200/30 px-4 py-1.5">
          <span className="label-mono !text-[10px] text-ink-300">
            Fixes from {packTitle} — also under each page in the Pages tab
          </span>
        </li>
      )}
      {tickets.map((t) => (
        <PackFixRow
          key={t.task_key}
          ticket={t}
          shareUrl={shareUrl}
          busy={packState.busyKey === t.task_key}
          onClose={() => packState.close(t.task_key)}
          onReopen={() => packState.reopen(t.task_key)}
          onRecheck={() => packState.recheck(t.task_key)}
          className="px-4 py-3"
        />
      ))}
    </>
  );
}

/** A phase the plan has no milestone for, carrying only pack fixes. Same shell, status
 *  derived from the tickets it holds. */
function PackOnlyPhaseCard({
  phaseKey,
  tickets,
  index,
  packTitle,
  packState,
  shareUrl,
}: {
  phaseKey: PhaseKey;
  tickets: Ticket[];
  index: number;
  packTitle?: string | null;
  packState: PackTicketsState;
  shareUrl: string | null;
}) {
  const verified = tickets.filter((t) => t.status === "verified_completed").length;
  const status: MilestoneStatus =
    verified === tickets.length
      ? "verified_completed"
      : tickets.some((t) => t.status !== "pending")
        ? "in_progress"
        : "pending";
  const meta = STATUS_META[status];
  const active = tickets.filter((t) => t.status !== "verified_completed");
  const done = tickets.filter((t) => t.status === "verified_completed");
  return (
    <PhaseCardShell
      statusLabel={meta.label}
      statusPill={meta.pill}
      title={phaseDisplayTitle(phaseKey, phaseKey)}
      blurb={phaseDisplayBlurb(phaseKey, "")}
      countLabel={`${verified}/${tickets.length} verified`}
      index={index}
    >
      {active.length > 0 && (
        <PackTicketRows tickets={active} packTitle={packTitle} packState={packState} shareUrl={shareUrl} />
      )}
      <DoneFold count={done.length}>
        {done.map((t) => (
          <PackFixRow
            key={t.task_key}
            ticket={t}
            shareUrl={shareUrl}
            busy={packState.busyKey === t.task_key}
            onClose={() => packState.close(t.task_key)}
            onReopen={() => packState.reopen(t.task_key)}
            onRecheck={() => packState.recheck(t.task_key)}
            className="px-4 py-3"
          />
        ))}
      </DoneFold>
    </PhaseCardShell>
  );
}

function TaskRow({
  task,
  shareUrl,
  onSetStatus,
}: {
  task: MilestoneTask;
  shareUrl: string | null;
  onSetStatus: (task: MilestoneTask, status: MilestoneStatus) => void;
}) {
  const [open, setOpen] = useState(false);
  const verifiedByCrawl = task.status === "verified_completed" && task.status_source === "crawl";
  // Already live when we first looked — real, but not work done here. Labelled separately
  // so the tracker never implies the owner published something they didn't.
  const preExisting = task.status === "verified_completed" && task.status_source === "baseline";
  const isVerified = task.status === "verified_completed";
  return (
    <li className="px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`font-medium ${isVerified ? "text-ink-300 line-through" : "text-ink"}`}>{task.label}</span>
          <p className="mt-0.5 text-sm text-ink-500">{task.action_required}</p>
          {/* what the customer must do on their site to complete + how we verify it */}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
              aria-expanded={open}
            >
              {open ? "Hide how-to" : "Show me how →"}
            </button>
            <span className="text-ink-300">{verifyHint(task)}</span>
            {verifiedByCrawl && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-300">
                <Check width={10} height={10} /> auto-detected live
              </span>
            )}
            {preExisting && (
              <span
                className="inline-flex items-center gap-1 rounded-full bg-ink/[0.06] px-1.5 py-0.5 font-medium text-ink-300"
                title="This was already live when we first checked your site, so it's marked done — but it isn't counted as a change you published."
              >
                <Check width={10} height={10} /> already in place
              </span>
            )}
          </div>
        </div>
        <StatusControl task={task} onSetStatus={onSetStatus} />
      </div>
      {open && (
        <TaskHowTo
          taskKey={task.task_key}
          label={task.label}
          currentState={task.current_state}
          actionRequired={task.action_required}
          howTo={task.how_to}
          prompts={task.prompts}
          diySteps={task.diy_steps}
          rawSnippet={task.raw_snippet}
          devBrief={task.dev_brief}
          shareUrl={shareUrl}
        />
      )}
    </li>
  );
}

// A compact 3-state segmented control. The owner can set any state; the weekly crawl can
// also flip a task to Verified on its own (shown with the "auto-detected" badge above).
function StatusControl({
  task,
  onSetStatus,
}: {
  task: MilestoneTask;
  onSetStatus: (task: MilestoneTask, status: MilestoneStatus) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={`Status for ${task.label}`}
      className="flex shrink-0 overflow-hidden rounded-lg border border-ink/10 text-[11px]"
    >
      {STATUS_ORDER.map((s) => {
        const active = task.status === s;
        const meta = STATUS_META[s];
        return (
          <button
            key={s}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onSetStatus(task, s)}
            className={`flex items-center gap-1 px-2 py-1 transition-colors ${
              active ? meta.pill.replace("ring-1", "") + " font-medium" : "text-ink-300 hover:bg-paper-200/60"
            }`}
            title={meta.label}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${active ? meta.dot : "bg-ink/15"}`} />
            <span className="hidden sm:inline">{meta.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function verifyHint(task: MilestoneTask): string {
  switch (task.verify_kind) {
    case "page":
      return `We'll verify automatically once ${task.verify_target ?? "this page"} is live.`;
    case "service":
    case "heading":
      return "We'll verify automatically once it appears on your site.";
    default:
      return "Mark this done yourself once complete.";
  }
}
