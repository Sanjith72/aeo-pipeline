"use client";

// One node on a phase's trail — the enemy a task renders as. Three visual states:
//   locked    → in shadow/fog + padlock (preview only; can't be completed)
//   active    → full colour, idle float, glowing ring; the next task to do
//   completed → defeated: faded enemy + a planted flag/check
// A real <button> so it's keyboard-reachable; size is modulated by the task's impact score.

import { enemyAt, type QuestTheme } from "@/lib/quest/theme";
import type { QuestTask } from "@/lib/quest/types";

export function EnemyNode({
  task,
  theme,
  onSelect,
}: {
  task: QuestTask;
  theme: QuestTheme;
  onSelect: (task: QuestTask) => void;
}) {
  const enemy = enemyAt(theme, task.enemy.slot);
  const state = task.nodeState;
  const engaged = state === "active" && task.status === "in_progress";
  const fontSize = `${2.1 * task.enemySize}rem`;

  const ariaState =
    state === "completed"
      ? task.verifiedLive
        ? "defeated, verified live"
        : task.coinsBanked
          ? "defeated"
          : "defeated, coins pending site verification"
      : state === "active"
        ? "ready to take on"
        : "locked";

  return (
    <button
      type="button"
      onClick={() => onSelect(task)}
      aria-label={`${enemy.name}${task.enemy.isBoss ? " (boss)" : ""} — ${task.label} — ${ariaState}`}
      className="group flex w-[7.5rem] flex-col items-center gap-1 rounded-xl p-1 text-center outline-none focus-visible:ring-2 focus-visible:ring-white/70"
    >
      <span className="relative flex h-16 w-16 items-center justify-center">
        {/* glow ring for the active enemy */}
        {state === "active" && (
          <span
            className="absolute inset-0 rounded-full"
            style={{ boxShadow: `0 0 0 3px ${theme.nodeAccent}, 0 0 22px 4px ${theme.nodeAccent}88` }}
          />
        )}
        {/* pending-verification ring: defeated, but the coins haven't banked yet — the map
            stays honest with the header tally until "Check my site" confirms it live */}
        {state === "completed" && !task.coinsBanked && (
          <span className="absolute inset-0 rounded-full border-2 border-dashed border-amber-400/50" aria-hidden />
        )}
        {/* the enemy glyph */}
        <span
          className={[
            "select-none leading-none transition-transform duration-300",
            state === "active" ? "animate-float-y" : "",
            engaged ? "animate-pulse" : "",
            state === "locked" ? "opacity-40 grayscale blur-[1px]" : "",
            state === "completed" ? "opacity-30 grayscale" : "",
            "group-hover:scale-110",
          ].join(" ")}
          style={{ fontSize }}
        >
          {enemy.glyph}
        </span>
        {/* boss crown */}
        {task.enemy.isBoss && state !== "completed" && (
          <span className="absolute -top-1.5 text-sm" aria-hidden>
            👑
          </span>
        )}
        {/* state badge */}
        {state === "locked" && (
          <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-black/70 text-[10px]" aria-hidden>
            🔒
          </span>
        )}
        {state === "completed" && (
          <span
            className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full text-[11px] text-white"
            style={{ background: task.verifiedLive ? "#10b981" : theme.nodeAccent }}
            aria-hidden
          >
            {task.verifiedLive ? "✓" : "🚩"}
          </span>
        )}
      </span>

      <span
        className={[
          "line-clamp-2 max-w-full text-[11px] font-medium leading-tight",
          // No extra opacity on locked labels — subInk alone keeps WCAG AA on the dark
          // stages; the dimmed/blurred glyph and padlock badge carry the locked state.
          state === "locked" ? theme.subInkClass : theme.inkClass,
        ].join(" ")}
        title={task.label}
      >
        {task.label}
      </span>

      {task.enemy.isBoss && (
        <span className="text-[9px] font-bold uppercase tracking-wider" style={{ color: theme.nodeAccent }}>
          {state === "completed" ? "Boss cleared" : "Final boss"}
        </span>
      )}
      {task.verifiedLive && (
        <span className="text-[9px] font-semibold uppercase tracking-wide text-emerald-400">Verified live ✓</span>
      )}
    </button>
  );
}
