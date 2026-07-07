"use client";

// The trail for one phase: a winding path threading every enemy node start→boss→reward,
// rendered on the phase's themed stage. Owns the per-phase interactions — opening the task
// dialog, the coin-burst on a defeat, and the chest/vault/cargo finale + "Phase Complete!"
// banner when the last enemy falls. Horizontal serpentine on desktop, vertical on mobile.

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { AnimatePresence, m, useReducedMotion } from "@/components/motion/primitives";
import { enemyAt, type QuestTheme } from "@/lib/quest/theme";
import type { QuestPhase, QuestTask } from "@/lib/quest/types";
import type { MilestoneStatus } from "@/lib/types";
import { CoinBurst, type BurstSpec } from "./CoinBurst";
import { EnemyNode } from "./EnemyNode";
import { TaskDetailPanel } from "./TaskDetailPanel";

type Pt = { x: number; y: number };

/** Lay `count` stops (enemies + the final reward) onto the stage as percentage coordinates —
 *  a gentle serpentine wave so it reads as one continuous trail. */
function layout(count: number, vertical: boolean): Pt[] {
  if (count <= 0) return [];
  const pad = vertical ? 9 : 8;
  const span = 100 - 2 * pad;
  return Array.from({ length: count }, (_, i) => {
    const t = count === 1 ? 0 : i / (count - 1);
    const along = pad + t * span;
    const wave = 50 + 25 * Math.sin(i * 0.95 + 0.6);
    return vertical ? { x: wave, y: along } : { x: along, y: wave };
  });
}

function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 480px)");
    const sync = () => setNarrow(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return narrow;
}

export function PhaseMap({
  phase,
  theme,
  shareUrl,
  onStatus,
  onPhaseComplete,
  visible = true,
}: {
  phase: QuestPhase;
  theme: QuestTheme;
  shareUrl: string | null;
  onStatus: (taskKey: string, status: MilestoneStatus) => void;
  onPhaseComplete: (phase: QuestPhase) => void;
  // False while the map is mounted but hidden (List toggle / another results tab). The
  // one-shot finale must not play — and be consumed — where nobody can see it.
  visible?: boolean;
}) {
  const reduced = useReducedMotion();
  const narrow = useIsNarrow();
  const gradId = useId();
  const [selected, setSelected] = useState<QuestTask | null>(null);
  const [burst, setBurst] = useState<BurstSpec | null>(null);
  const [finale, setFinale] = useState(false);
  const [announce, setAnnounce] = useState(""); // polite SR confirmation, motion-independent
  const burstKey = useRef(0);
  // Stable so CoinBurst's one-shot dismiss timer isn't reset on every parent re-render.
  const handleBurstDone = useCallback(() => setBurst(null), []);

  const stops = layout(phase.tasks.length + 1, narrow);
  const chest = stops[stops.length - 1] ?? { x: 50, y: 90 };
  // The chest only pays out once every task in the phase has banked (model.ts) — the finale
  // must not claim bonus coins the header tally won't show.
  const chestBanked = phase.tasks.every((t) => t.coinsBanked);
  const height = narrow
    ? Math.max(440, (phase.tasks.length + 1) * 116)
    : Math.max(360, 300 + Math.max(0, phase.tasks.length - 4) * 26);

  // Keep the open dialog's data in sync with model updates (status flips after completion).
  const live = selected ? phase.tasks.find((t) => t.id === selected.id) ?? selected : null;

  // Fire the chest finale exactly when the phase transitions to complete (never on a reload
  // that's already complete). If the completion lands while the map is hidden (e.g. the last
  // task was ticked off in the List view of the shared tracker), hold the un-consumed latch
  // so the celebration — and quest_phase_complete, via onPhaseComplete — fires on first reveal.
  const wasComplete = useRef(phase.isComplete);
  useEffect(() => {
    if (phase.isComplete && !wasComplete.current) {
      if (!visible) return;
      wasComplete.current = true;
      onPhaseComplete(phase);
      // The coin-burst confetti is motion-only, but the "Phase Complete!" banner is a required
      // deliverable — it always shows (collapsing to a plain fade under reduced motion). The
      // chest burst only fires when the bonus actually banked — no raining coins that the
      // header tally won't count.
      if (!reduced && chestBanked) {
        burstKey.current += 1;
        setBurst({ key: burstKey.current, xPct: chest.x, yPct: chest.y, coins: phase.chestBonus, color: theme.coinColor, bonus: true });
      }
      setFinale(true);
    } else if (!phase.isComplete) {
      wasComplete.current = false; // allow the finale to replay if a task is later un-marked
    }
  }, [phase.isComplete, reduced, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  // The banner teardown lives in its own effect, keyed only on `finale`: if it shared the
  // effect above, a mid-celebration `visible` flip would run that effect's cleanup — cancelling
  // the timer without rescheduling it — and strand the overlay on permanently.
  useEffect(() => {
    if (!finale) return;
    const t = setTimeout(() => setFinale(false), reduced ? 2200 : 2600);
    return () => clearTimeout(t);
  }, [finale, reduced]);

  function complete(task: QuestTask) {
    const p = stops[task.index] ?? chest;
    if (!reduced) {
      burstKey.current += 1;
      // A crawlable task marked by hand hasn't banked its coins yet — the burst wears the
      // amber "pending" ring (matching the node treatment) instead of reading as a payout.
      setBurst({
        key: burstKey.current,
        xPct: p.x,
        yPct: p.y,
        coins: task.coins,
        color: theme.coinColor,
        pending: task.milestoneTask.verify_kind !== "manual",
      });
    }
    // Motion-independent confirmation so reduced-motion + screen-reader users still get feedback
    // (the coin burst is suppressed for them). "Verified live" is intentionally NOT claimed here —
    // that only comes from the server crawl, surfaced via the header verify note. Coins are only
    // claimed for off-site tasks (self-report banks them); crawlable marks stay pending.
    setAnnounce(
      task.milestoneTask.verify_kind === "manual"
        ? `${enemyAt(theme, task.enemy.slot).name} defeated — +${task.coins} coins earned.`
        : `${enemyAt(theme, task.enemy.slot).name} defeated — ${task.coins} coins pending until your site is verified.`,
    );
    setSelected(null);
    onStatus(task.id, "verified_completed");
  }

  function working(task: QuestTask) {
    setSelected(null);
    onStatus(task.id, "in_progress");
  }

  return (
    <div
      className={`relative mt-3 overflow-hidden rounded-xl ${theme.surfaceClass} ${theme.inkClass} grain`}
      style={{ height }}
    >
      {/* trail */}
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <linearGradient id={gradId}>
            <stop offset="0%" stopColor={theme.pathDoneColor} />
            <stop offset="100%" stopColor={theme.pathDoneColor} />
          </linearGradient>
        </defs>
        {stops.slice(0, -1).map((p, i) => {
          const next = stops[i + 1];
          const cleared = phase.tasks[i]?.status === "verified_completed";
          return (
            <line
              key={i}
              x1={p.x}
              y1={p.y}
              x2={next.x}
              y2={next.y}
              stroke={cleared ? theme.pathDoneColor : theme.pathColor}
              strokeWidth={2.5}
              strokeLinecap="round"
              strokeDasharray={cleared ? undefined : "5 6"}
              vectorEffect="non-scaling-stroke"
              opacity={cleared ? 0.95 : 0.8}
            />
          );
        })}
      </svg>

      {/* enemy nodes */}
      {phase.tasks.map((task) => {
        const p = stops[task.index];
        return (
          <div
            key={task.id}
            className="absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${p.x}%`, top: `${p.y}%` }}
          >
            <EnemyNode task={task} theme={theme} onSelect={setSelected} />
          </div>
        );
      })}

      {/* the reward chest at the end of the trail */}
      <div
        className="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
        style={{ left: `${chest.x}%`, top: `${chest.y}%` }}
      >
        <m.span
          className="text-4xl leading-none drop-shadow"
          animate={
            reduced
              ? undefined
              : phase.isComplete
                ? { scale: [1, 1.35, 1], rotate: [0, -8, 8, 0] }
                : { opacity: 0.55 }
          }
          transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          style={{ opacity: phase.isComplete ? 1 : 0.55 }}
          aria-hidden
        >
          {theme.chestGlyph}
        </m.span>
        <span className={`mt-1 text-[10px] font-semibold uppercase tracking-wide ${theme.subInkClass}`}>
          {theme.rewardLabel}
        </span>
      </div>

      <CoinBurst burst={burst} onDone={handleBurstDone} />

      {/* motion-independent polite announcement for AT / reduced-motion users */}
      <div className="sr-only" role="status" aria-live="polite">
        {announce}
      </div>

      {/* phase-complete celebration — banner always shows; under reduced motion it just fades */}
      <AnimatePresence>
        {finale && (
          <m.div
            className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <m.div
              className="rounded-2xl border border-white/30 bg-black/50 px-6 py-4 text-center backdrop-blur-sm"
              initial={reduced ? { opacity: 0 } : { scale: 0.7, y: 10 }}
              animate={reduced ? { opacity: 1 } : { scale: 1, y: 0 }}
              transition={reduced ? { duration: 0.3 } : { type: "spring", stiffness: 220, damping: 16 }}
            >
              <p className="text-2xl">{theme.chestGlyph}✨</p>
              <p className="mt-1 text-lg font-bold text-white">Phase Complete!</p>
              <p className="text-xs text-white/70">
                {chestBanked
                  ? `${theme.rewardLabel} opened · +${phase.chestBonus} bonus coins`
                  : `Verify your site to bank +${phase.chestBonus} bonus coins`}
              </p>
            </m.div>
          </m.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {live && (
          <TaskDetailPanel
            key={live.id}
            task={live}
            theme={theme}
            shareUrl={shareUrl}
            readOnly={live.nodeState === "locked"}
            onClose={() => setSelected(null)}
            onComplete={complete}
            onWorking={working}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
