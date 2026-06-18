"use client";

// The Implementation Dashboard — the "Final Plan" as persisted, trackable milestones.
// Unlike the localStorage checklist, progress here lives on the server (per site) and
// advances two ways: the owner toggles a task, OR the weekly verification crawl detects
// the recommended artifact live on the site and auto-marks it "Verified ✓". The headline
// progress bar reflects verified-vs-total across every milestone.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Milestone, MilestoneDashboard as Dashboard, MilestoneStatus, MilestoneTask, StructuredPlan } from "@/lib/types";
import { Check } from "./ui/icons";

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

export function MilestoneDashboard({
  domain,
  plan,
  businessName,
  cmsType,
}: {
  domain: string;
  plan: StructuredPlan;
  businessName: string;
  // Detected CMS ('wordpress' | 'shopify' | 'unknown' | null) — sent with the sync so the
  // dashboard's "I'll do it myself" steps come back tailored to the platform.
  cmsType?: string | null;
}) {
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);

  // Persist (sync) the generated plan as milestones on mount, then render what comes
  // back. Idempotent server-side, so this is safe on every revisit — existing progress
  // and crawl-verified status are preserved.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .syncMilestones({ domain, name: businessName || undefined, plan, cms_type: cmsType ?? undefined })
      .then((d) => !cancelled && setDash(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [domain, businessName, plan, cmsType]);

  const setStatus = useCallback(
    async (task: MilestoneTask, status: MilestoneStatus) => {
      if (task.status === status) return;
      // optimistic: reflect immediately, reconcile with the server's recomputed roll-up
      setDash((prev) => prev && patchTask(prev, task.task_key, status));
      api.track("milestone_task_status", { task_key: task.task_key, status });
      try {
        setDash(await api.setMilestoneTask({ domain, task_key: task.task_key, status }));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [domain],
  );

  // Revoke the current share link and swap in the fresh token. Updating share_token on the
  // dashboard re-derives shareUrl, which flows down to every task expander — so all the
  // mailto: links and textareas below rebuild with the new link automatically.
  const rotateLink = useCallback(async () => {
    if (
      !window.confirm(
        "This will permanently disable the current link for anyone who has it. Are you sure?",
      )
    )
      return;
    setRotating(true);
    setError(null);
    try {
      const { share_token } = await api.rotateShareLink(domain);
      setDash((prev) => (prev ? { ...prev, share_token } : prev));
      api.track("dev_handoff_link_rotated", {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRotating(false);
    }
  }, [domain]);

  const checkSite = useCallback(async () => {
    setVerifying(true);
    setVerifyNote(null);
    try {
      const res = await api.verifyMilestones(domain);
      setDash(res.dashboard);
      const n = res.summary.newly_verified;
      setVerifyNote(
        n > 0
          ? `Nice — we found ${n} change${n === 1 ? "" : "s"} live and marked ${n === 1 ? "it" : "them"} verified.`
          : "We checked your site — nothing new is live yet. Publish a change, then check again.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifying(false);
    }
  }, [domain]);

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
  // The Developer Handoff link points at this app's public read-only route. Built from
  // the share token the owner endpoints mint; null until it (and `window`) are available.
  const shareUrl =
    dash.share_token && typeof window !== "undefined"
      ? `${window.location.origin}/share/${dash.share_token}`
      : null;

  return (
    <div className="space-y-6">
      <ManageAccess shareUrl={shareUrl} rotating={rotating} onRotate={rotateLink} />

      {/* headline progress */}
      <div className="card p-5 sm:p-6">
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">Your implementation roadmap</h3>
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

// ── clipboard copy with a graceful fallback ─────────────────────────────────────
// navigator.clipboard.writeText rejects in restricted contexts (no transient user
// activation, a sandboxed iframe, a tight permissions-policy). Failing silently leaves the
// user clicking a dead button, so on rejection we instead SELECT the source text and prompt
// a keyboard copy. All three copy buttons in this file share this one hook.

type CopyPhase = "idle" | "copied" | "manual";

// The keyboard hint, OS-aware. Only ever rendered after a click (the "manual" phase), so
// reading `navigator` here can't cause an SSR/hydration mismatch on first paint.
function manualCopyHint(): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);
  return mac ? "Press ⌘+C to copy" : "Press Ctrl+C to copy";
}

// Highlight a text field (input/textarea) so a manual Ctrl/⌘+C grabs its full value.
function selectField(el: HTMLInputElement | HTMLTextAreaElement | null) {
  if (!el) return;
  el.focus();
  el.select();
}

// Highlight the contents of a non-editable block (our <pre><code> snippet) via a Range.
function selectBlock(el: HTMLElement | null) {
  if (!el || typeof window === "undefined") return;
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

function clearWindowSelection() {
  if (typeof window !== "undefined") window.getSelection()?.removeAllRanges();
}

function useCopyAction({
  getText,
  selectFallback,
  clearSelection,
  copiedMs = 1500,
  onCopied,
}: {
  getText: () => string | null;
  selectFallback: () => void;
  clearSelection: () => void;
  copiedMs?: number;
  onCopied?: () => void;
}): { phase: CopyPhase; copy: () => void } {
  const [phase, setPhase] = useState<CopyPhase>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = useCallback(() => {
    const text = getText();
    if (text == null) return;
    if (timer.current) clearTimeout(timer.current);
    const revert = (ms: number, after?: () => void) => {
      timer.current = setTimeout(() => {
        setPhase("idle");
        after?.();
      }, ms);
    };
    const succeed = () => {
      setPhase("copied");
      onCopied?.();
      revert(copiedMs);
    };
    // No Clipboard API, or it rejected → select the text and ask for a keyboard copy,
    // clearing the selection when the prompt reverts after 2s.
    const fallback = () => {
      selectFallback();
      setPhase("manual");
      revert(2000, clearSelection);
    };
    try {
      const result = navigator.clipboard?.writeText(text);
      if (result && typeof result.then === "function") result.then(succeed, fallback);
      else fallback();
    } catch {
      fallback();
    }
  }, [getText, selectFallback, clearSelection, copiedMs, onCopied]);

  return { phase, copy };
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
      {open && <TaskGuide task={task} shareUrl={shareUrl} />}
    </li>
  );
}

// The how-to expander: a two-tab panel. "I'll do it myself" (default) walks the owner
// through the change on their own CMS with a copy-paste snippet; "Send to Developer" is the
// paste-ready brief + read-only tracking link to hand off. Defaulting to self-serve nudges
// owners to ship the small changes themselves.
type GuideTab = "diy" | "developer";

function TaskGuide({ task, shareUrl }: { task: MilestoneTask; shareUrl: string | null }) {
  const [tab, setTab] = useState<GuideTab>("diy");
  return (
    <div className="step-in mt-3 space-y-3">
      {task.how_to && (
        <div className="rounded-lg border border-ink/[0.06] bg-paper-200/40 px-3.5 py-3 text-sm">
          <span className="label-mono">How to do it</span>
          <p className="mt-0.5 leading-relaxed text-ink-500">{task.how_to}</p>
        </div>
      )}
      <div
        role="tablist"
        aria-label="Implementation options"
        className="flex gap-1 rounded-lg border border-ink/10 bg-paper-200/60 p-1 text-[12px]"
      >
        {(
          [
            { id: "diy" as const, label: "I'll do it myself" },
            { id: "developer" as const, label: "Send to Developer" },
          ]
        ).map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`flex-1 rounded-md px-3 py-1.5 transition-all duration-200 ${
              tab === id ? "bg-paper-100 font-medium text-ink shadow-card" : "text-ink-300 hover:text-ink-500"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "diy" ? <DiyGuide task={task} /> : <DeveloperHandoff task={task} shareUrl={shareUrl} />}
    </div>
  );
}

// "I'll do it myself" — the CMS-aware numbered steps plus, when the task has one, the exact
// snippet to paste in a dark code block with a one-click "Copy Code" button.
function DiyGuide({ task }: { task: MilestoneTask }) {
  const steps = task.diy_steps ?? [];
  const snippet = task.raw_snippet ?? null;
  const codeRef = useRef<HTMLElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => snippet,
    selectFallback: () => selectBlock(codeRef.current),
    clearSelection: clearWindowSelection,
    copiedMs: 2000,
    onCopied: () => api.track("diy_snippet_copied", { task_key: task.task_key }),
  });

  return (
    <div className="rounded-lg border border-ink/[0.08] bg-paper-200/30 px-3.5 py-3 text-sm">
      {steps.length > 0 ? (
        <ol className="ml-4 list-decimal space-y-1.5 leading-relaxed text-ink-500 marker:text-ink-300">
          {steps.map((step, i) => (
            <li key={i} className="pl-1">
              {step}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-ink-500">{task.action_required}</p>
      )}
      {snippet && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="label-mono">Code snippet</span>
            <button
              type="button"
              onClick={copy}
              className="btn-primary !py-1 text-[11px]"
              aria-label="Copy code snippet to clipboard"
            >
              {phase === "copied" ? "Copied!" : phase === "manual" ? manualCopyHint() : "Copy Code"}
            </button>
          </div>
          <pre className="max-h-72 w-full overflow-auto rounded-md border border-ink/10 bg-paper p-3 font-mono text-[11px] leading-relaxed text-emerald-200 shadow-card">
            <code ref={codeRef}>{snippet}</code>
          </pre>
        </div>
      )}
    </div>
  );
}

// Developer Handoff — a paste-ready message (greeting + the server-generated technical
// brief + the read-only tracking link) the owner hands to their web developer, with
// one-click copy and a pre-filled mailto.
function DeveloperHandoff({ task, shareUrl }: { task: MilestoneTask; shareUrl: string | null }) {
  const brief = task.dev_brief?.trim() || task.action_required;
  const message = buildHandoffMessage(brief, shareUrl);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => message,
    selectFallback: () => selectField(textareaRef.current),
    clearSelection: () => textareaRef.current?.setSelectionRange(0, 0),
    onCopied: () => api.track("dev_handoff_copied", { task_key: task.task_key }),
  });

  const mailto =
    `mailto:?subject=${encodeURIComponent(`Website update request: ${task.label}`)}` +
    `&body=${encodeURIComponent(message)}`;

  return (
    <div className="rounded-lg border border-accent/30 bg-accent/[0.04] px-3.5 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="label-mono !text-accent">Developer handoff</span>
        {shareUrl && (
          <span className="font-mono text-[11px] text-ink-300">read-only tracking link included</span>
        )}
      </div>
      <p className="mt-1 text-xs text-ink-500">
        Hand this to your web developer — it has exact, paste-ready instructions
        {shareUrl ? " and a live link to track progress." : "."}
      </p>
      <textarea
        ref={textareaRef}
        readOnly
        value={message}
        rows={9}
        onFocus={(e) => e.currentTarget.select()}
        className="mt-2 w-full resize-y rounded-md border border-ink/10 bg-paper p-2.5 font-mono text-[11px] leading-relaxed text-ink-700"
        aria-label={`Developer brief for ${task.label}`}
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={copy} className="btn-primary !py-1.5 text-[12px]">
          {phase === "copied" ? "Copied ✓" : phase === "manual" ? manualCopyHint() : "Copy brief & link"}
        </button>
        <a
          href={mailto}
          onClick={() => api.track("dev_handoff_emailed", { task_key: task.task_key })}
          className="btn-ghost !py-1.5 text-[12px]"
        >
          Send via email
        </a>
      </div>
      {!shareUrl && (
        <p className="mt-2 text-[11px] text-ink-300">
          The shareable tracking link will appear here once your tracker finishes setting up.
        </p>
      )}
    </div>
  );
}

function buildHandoffMessage(brief: string, shareUrl: string | null): string {
  const lines = [
    "Hi,",
    "",
    "Could you make the following change to our website? The technical details are below.",
    "",
    brief,
  ];
  if (shareUrl) {
    lines.push(
      "",
      "You can see this task (and our full implementation plan) on this live, read-only tracker — it updates automatically as changes go live:",
      shareUrl,
    );
  }
  lines.push("", "Thanks!");
  return lines.join("\n");
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

// Optimistic local patch: set one task's status and re-derive the affected counts so the
// UI updates instantly before the server's recomputed dashboard arrives.
function patchTask(dash: Dashboard, taskKey: string, status: MilestoneStatus): Dashboard {
  const milestones = dash.milestones.map((m) => ({
    ...m,
    tasks: m.tasks.map((t) => (t.task_key === taskKey ? { ...t, status, status_source: "manual" as const } : t)),
  }));
  const all = milestones.flatMap((m) => m.tasks);
  const verified = all.filter((t) => t.status === "verified_completed").length;
  const inProgress = all.filter((t) => t.status === "in_progress").length;
  const total = all.length;
  return {
    milestones,
    progress: { total, verified, in_progress: inProgress, pct: total ? Math.round((verified / total) * 100) : 0 },
  };
}
