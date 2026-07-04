"use client";

// The Implementation Dashboard — the "Final Plan" as persisted, trackable milestones,
// rendered as the plain List view of the tracker. Progress lives on the server (per site)
// and advances two ways: the owner toggles a task, OR the weekly verification crawl detects
// the recommended artifact live on the site and auto-marks it "Verified ✓". The data itself
// is owned by the shared QuestTracker (one instance in TrackerView, read by both the List
// and the Quest Map), so the two views never disagree.

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Milestone, MilestoneStatus, MilestoneTask } from "@/lib/types";
import { manualCopyHint, selectField, useCopyAction } from "@/lib/copy";
import { Check } from "./ui/icons";
import { TaskHowTo } from "./TaskHowTo";
import type { QuestTracker } from "./quest/useQuestTracker";

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

export function MilestoneDashboard({ tracker }: { tracker: QuestTracker }) {
  const { dash, error, verifying, lastVerify, rotating, shareUrl, checkSite } = tracker;
  const verifyNote = lastVerify
    ? lastVerify.newlyVerified > 0
      ? `Nice — we found ${lastVerify.newlyVerified} change${lastVerify.newlyVerified === 1 ? "" : "s"} live and marked ${lastVerify.newlyVerified === 1 ? "it" : "them"} verified.`
      : "We checked your site — nothing new is live yet. Publish a change, then check again."
    : null;

  // Fire the list event before the shared write so telemetry keeps naming this surface.
  // The segmented control re-sends the current status on a same-state click — skip those.
  const setStatus = (task: MilestoneTask, status: MilestoneStatus) => {
    if (task.status === status) return;
    api.track("milestone_task_status", { task_key: task.task_key, status });
    void tracker.setStatus(task.task_key, status);
  };

  // Rotation is destructive for anyone holding the old link — hard-confirm before the
  // shared tracker revokes it and re-derives shareUrl for every expander in both views.
  const rotateLink = () => {
    if (
      !window.confirm(
        "This will permanently disable the current link for anyone who has it. Are you sure?",
      )
    )
      return;
    void tracker.rotateShareLink();
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

  return (
    <div className="space-y-6">
      <ManageAccess shareUrl={shareUrl} rotating={rotating} onRotate={rotateLink} />

      {/* headline progress — the visible "Your implementation roadmap" heading lives in
          TrackerView (above the List ⇄ Map toggle), so this card shows the count alone; the
          sr-only heading keeps the outline from filing the roadmap under "Manage developer
          access" (the previous card's h3). */}
      <div className="card p-5 sm:p-6">
        <h3 className="sr-only">Roadmap progress</h3>
        <div className="mb-1.5 flex items-baseline justify-end">
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
            We re-check your site every week and tick off changes automatically.
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
        {verifyNote && (
          <p className="step-in mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            {verifyNote}
          </p>
        )}
        {done && (
          <p className="step-in mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            Every milestone is verified live — your business is set up to be the one AI recommends. 🎉
          </p>
        )}
      </div>

      {milestones.map((m, i) => (
        <MilestoneCard key={m.milestone_key} milestone={m} index={i} shareUrl={shareUrl} onSetStatus={setStatus} />
      ))}
    </div>
  );
}

// Manage Developer Access — shows the current master share link and lets the owner kill a
// leaked one and issue a fresh link in a single click (with a hard confirm). Sits above the
// progress bar so link hygiene is the first thing an owner sees.
function ManageAccess({
  shareUrl,
  rotating,
  onRotate,
}: {
  shareUrl: string | null;
  rotating: boolean;
  onRotate: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => shareUrl,
    selectFallback: () => selectField(inputRef.current),
    clearSelection: () => inputRef.current?.setSelectionRange(0, 0),
  });
  return (
    <div className="card p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">Manage developer access</h3>
          <p className="mt-0.5 max-w-xl text-sm text-ink-500">
            One read-only link gives a developer your whole plan and live progress — no login. Share
            it freely; revoke it the moment it should stop working.
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

function MilestoneCard({
  milestone,
  index,
  shareUrl,
  onSetStatus,
}: {
  milestone: Milestone;
  index: number;
  shareUrl: string | null;
  onSetStatus: (task: MilestoneTask, status: MilestoneStatus) => void;
}) {
  const meta = STATUS_META[milestone.status];
  const verified = milestone.tasks.filter((t) => t.status === "verified_completed").length;
  return (
    <div
      className="step-in overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100"
      style={{ animationDelay: `${Math.min(index, 4) * 70}ms` }}
    >
      <div className="border-b border-ink/[0.06] bg-paper-200/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${meta.pill}`}>{meta.label}</span>
          <span className="font-semibold">{milestone.title}</span>
          <span className="font-mono text-xs text-ink-300">
            {verified}/{milestone.tasks.length} verified
          </span>
        </div>
        {milestone.blurb && <p className="mt-1 text-xs text-ink-300">{milestone.blurb}</p>}
      </div>
      <ul className="divide-y divide-ink/[0.06]">
        {milestone.tasks.map((t) => (
          <TaskRow key={t.task_key} task={t} shareUrl={shareUrl} onSetStatus={onSetStatus} />
        ))}
      </ul>
    </div>
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
