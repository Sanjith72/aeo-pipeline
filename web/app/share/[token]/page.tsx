"use client";

// Developer Handoff — the public, read-only view of a client's implementation plan.
// Anyone with the share link lands here (no login): they see the same milestones,
// progress bar, and per-task developer instructions the owner sees, but with NO controls.
// The weekly verification crawl keeps it current, so it doubles as a live tracker the
// owner can hand to whoever is doing the work.

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { phaseDisplayTitle } from "@/lib/phases";
import type { Milestone, MilestoneTask, SharedPlanResponse } from "@/lib/types";
import { STATUS_META } from "@/components/MilestoneDashboard";
import { Check } from "@/components/ui/icons";

export default function SharedPlanPage() {
  const params = useParams<{ token: string }>();
  const token = Array.isArray(params.token) ? params.token[0] : params.token;
  const [data, setData] = useState<SharedPlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .getSharedPlan(token)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:py-14">
      {error ? (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/[0.06] p-6 text-sm text-rose-200">
          {error}
        </div>
      ) : !data ? (
        <div className="flex items-center gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-6 text-sm text-ink-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          Loading the plan…
        </div>
      ) : (
        <SharedPlanView data={data} />
      )}
    </main>
  );
}

function SharedPlanView({ data }: { data: SharedPlanResponse }) {
  const { progress, milestones, business_name } = data;
  return (
    <div className="step-in">
      <div className="mb-6">
        <span className="label-mono inline-flex items-center gap-2 !text-accent">Read-only · live tracker</span>
        <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">
          Implementation plan{business_name ? ` — ${business_name}` : ""}
        </h1>
        <p className="mt-1 text-sm text-ink-500">
          The changes below were recommended to improve how AI assistants find and recommend this
          business. Each task has developer-ready instructions. This view updates automatically as
          changes go live — no login needed.
        </p>
      </div>

      {/* progress */}
      <div className="card mb-6 p-5 sm:p-6">
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-base font-semibold">Progress</h2>
          <span className="font-mono text-xs text-ink-500">
            {progress.verified} / {progress.total} verified
          </span>
        </div>
        <div
          className="h-2 overflow-hidden rounded-full bg-ink/[0.07]"
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
      </div>

      <div className="space-y-5">
        {milestones.map((m) => (
          <ReadOnlyMilestone key={m.milestone_key} milestone={m} />
        ))}
      </div>
    </div>
  );
}

function ReadOnlyMilestone({ milestone }: { milestone: Milestone }) {
  const meta = STATUS_META[milestone.status];
  const verified = milestone.tasks.filter((t) => t.status === "verified_completed").length;
  return (
    <div className="overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100">
      <div className="border-b border-ink/[0.06] bg-paper-200/40 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${meta.pill}`}>{meta.label}</span>
          <span className="font-semibold">{phaseDisplayTitle(milestone.milestone_key, milestone.title)}</span>
          <span className="font-mono text-xs text-ink-300">
            {verified}/{milestone.tasks.length} verified
          </span>
        </div>
        {milestone.blurb && <p className="mt-1 text-xs text-ink-300">{milestone.blurb}</p>}
      </div>
      <ul className="divide-y divide-ink/[0.06]">
        {milestone.tasks.map((t) => (
          <ReadOnlyTask key={t.task_key} task={t} />
        ))}
      </ul>
    </div>
  );
}

function ReadOnlyTask({ task }: { task: MilestoneTask }) {
  const [open, setOpen] = useState(false);
  const meta = STATUS_META[task.status];
  const isVerified = task.status === "verified_completed";
  return (
    <li className="px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`font-medium ${isVerified ? "text-ink-300 line-through" : "text-ink"}`}>{task.label}</span>
          <p className="mt-0.5 text-sm text-ink-500">{task.action_required}</p>
          {task.dev_brief && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="mt-1 text-xs text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
              aria-expanded={open}
            >
              {open ? "Hide developer instructions" : "View developer instructions →"}
            </button>
          )}
        </div>
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${meta.pill}`}>
          {isVerified && <Check width={10} height={10} />}
          {meta.label}
        </span>
      </div>
      {open && task.dev_brief && (
        <pre className="step-in mt-2 max-h-80 w-full overflow-auto whitespace-pre-wrap rounded-md border border-ink/10 bg-paper p-2.5 font-mono text-[11px] leading-relaxed text-ink-700">
          {task.dev_brief}
        </pre>
      )}
    </li>
  );
}
