"use client";

// The task detail dialog opened from a node. Shows what the task is, why it matters, and
// the action(s) to complete it, reusing the shipped how-to / DIY guide (the Developer
// handoff lives on the Strategy tab, not in the map). "Mark Complete" defeats the enemy +
// drops coins immediately (manual attestation); the weekly crawl later adds the honest
// "Verified live ✓" flourish. Read-only for locked tasks.

import { useEffect, useRef } from "react";

import { m, useReducedMotion } from "@/components/motion/primitives";
import { TaskHowTo } from "@/components/TaskHowTo";
import { enemyAt, type QuestTheme } from "@/lib/quest/theme";
import type { QuestTask } from "@/lib/quest/types";

function whyItMatters(task: QuestTask): string {
  const impactWord =
    task.impact >= 0.8
      ? "one of the highest-impact moves in this phase"
      : task.impact >= 0.5
        ? "a solid, needle-moving win"
        : "a quick cleanup win";
  const cat =
    task.category === "visibility"
      ? "It builds off-site visibility — the listings and reviews AI assistants lean on when they make recommendations."
      : "It strengthens the on-site content and structure answer engines read when deciding who to cite.";
  return `This is ${impactWord}. ${cat}`;
}

export function TaskDetailPanel({
  task,
  theme,
  shareUrl,
  readOnly,
  onClose,
  onComplete,
  onWorking,
}: {
  task: QuestTask;
  theme: QuestTheme;
  shareUrl: string | null;
  readOnly: boolean;
  onClose: () => void;
  onComplete: (task: QuestTask) => void;
  onWorking: (task: QuestTask) => void;
}) {
  const reduced = useReducedMotion();
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const enemy = enemyAt(theme, task.enemy.slot);
  const isDone = task.nodeState === "completed";

  // Focus management: move focus into the dialog, keep Tab/Shift+Tab cycling within it, close
  // on Escape, and restore focus to the element that opened it (the node) on unmount.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const root = dialogRef.current;
      if (!root) return;
      const focusables = Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (focusables.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !root.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !root.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);

  // The parent (PhaseMap) owns the <AnimatePresence>, so it stays mounted while this panel
  // leaves — that's what lets the exit animation below actually play on close.
  return (
    <m.div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-6"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      >
        <m.div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-label={task.label}
          tabIndex={-1}
          className="card max-h-[88vh] w-full max-w-lg overflow-y-auto p-5 sm:p-6 outline-none"
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: 24, scale: 0.98 }}
          animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: 24, scale: 0.98 }}
          transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="text-3xl leading-none" aria-hidden>
                {enemy.glyph}
              </span>
              <div>
                <p className="label-mono !text-[10px]">
                  {task.enemy.isBoss ? "Final boss" : enemy.name}
                </p>
                <h3 className="text-base font-semibold leading-tight">{task.label}</h3>
              </div>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="btn-ghost shrink-0 !rounded-lg !px-2.5 !py-1.5 text-sm"
            >
              ✕
            </button>
          </div>

          <p className="mt-4 rounded-lg border border-ink/[0.08] bg-paper-200/40 px-3.5 py-3 text-sm text-ink-500">
            {whyItMatters(task)}
          </p>

          {/* The DIY guide: context ("Where you are now" / "What to do" / "How to do it")
              plus the do-it-yourself steps — same component the Strategy list uses. The
              Developer Handoff deliberately does NOT live here anymore: it has its own
              section on the Strategy tab, so the map stays a play surface. */}
          <div className="mt-3">
            <TaskHowTo
              taskKey={task.milestoneTask.task_key}
              label={task.milestoneTask.label}
              currentState={task.milestoneTask.current_state}
              actionRequired={task.milestoneTask.action_required}
              howTo={task.milestoneTask.how_to}
              prompts={task.milestoneTask.prompts}
              diySteps={task.milestoneTask.diy_steps}
              rawSnippet={task.milestoneTask.raw_snippet}
              devBrief={task.milestoneTask.dev_brief}
              shareUrl={shareUrl}
              showDeveloper={false}
            />
            <p className="mt-2 text-[11px] text-ink-300">
              Working with a developer? The paste-ready brief for this task lives on the{" "}
              <span className="font-medium text-ink-500">Strategy</span> tab, under each step and in
              the Developer handoff section.
            </p>
          </div>

          {/* actions */}
          {readOnly ? (
            <p className="mt-5 rounded-lg border border-ink/10 bg-paper-200/40 px-3.5 py-2.5 text-center text-xs text-ink-300">
              {isDone
                ? "This one's already cleared. Nice work."
                : "Defeat the enemies before this one on the trail first."}
            </p>
          ) : isDone ? (
            <p className="mt-5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2.5 text-center text-sm text-emerald-300">
              {task.verifiedLive ? "Verified live on your site ✓" : "Marked complete — coins earned."}
            </p>
          ) : (
            <div className="mt-5 flex flex-wrap gap-2">
              <button type="button" onClick={() => onComplete(task)} className="btn-accent flex-1">
                Mark complete — defeat {task.enemy.isBoss ? "the boss" : "enemy"}
              </button>
              {task.status !== "in_progress" && (
                <button type="button" onClick={() => onWorking(task)} className="btn-ghost text-[13px]">
                  Working on it
                </button>
              )}
            </div>
          )}
        </m.div>
      </m.div>
  );
}
