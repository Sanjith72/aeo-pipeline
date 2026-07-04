"use client";

// One phase accordion tab. The header is always visible (collapsed or open): phase name, a
// persistent progress bar, the running coin total, and a padlock while the phase is still
// locked. Locked phases can be expanded to *preview* the map (read-only) but not completed.
// When a phase unlocks, the padlock plays a quick unlocking beat.

import { useEffect, useRef, useState } from "react";

import { m, Tally, useReducedMotion } from "@/components/motion/primitives";
import type { QuestTheme } from "@/lib/quest/theme";
import type { QuestPhase } from "@/lib/quest/types";
import type { MilestoneStatus } from "@/lib/types";
import { PhaseMap } from "./PhaseMap";

export function PhaseTab({
  phase,
  theme,
  open,
  onToggle,
  shareUrl,
  onStatus,
  onPhaseComplete,
  visible = true,
}: {
  phase: QuestPhase;
  theme: QuestTheme;
  open: boolean;
  onToggle: () => void;
  shareUrl: string | null;
  onStatus: (taskKey: string, status: MilestoneStatus) => void;
  onPhaseComplete: (phase: QuestPhase) => void;
  // False while the map is mounted but hidden — one-shot beats hold until it can be seen.
  visible?: boolean;
}) {
  const reduced = useReducedMotion();
  const [justUnlocked, setJustUnlocked] = useState(false);
  const wasLocked = useRef(phase.locked);

  useEffect(() => {
    if (wasLocked.current && !phase.locked) {
      // Unlocked while hidden (e.g. from the List view): keep the latch so the beat
      // plays the first time the map is actually shown instead of burning invisibly.
      if (!visible) return;
      wasLocked.current = false;
      setJustUnlocked(true);
      return;
    }
    wasLocked.current = phase.locked;
  }, [phase.locked, visible]);

  // Teardown in its own effect, keyed only on the badge state: if it shared the effect
  // above, a mid-beat `visible` flip would cancel the timer without rescheduling it and
  // strand the "Unlocked!" badge (and its aria-live text) for the whole session.
  useEffect(() => {
    if (!justUnlocked) return;
    const t = setTimeout(() => setJustUnlocked(false), 1400);
    return () => clearTimeout(t);
  }, [justUnlocked]);

  const panelId = `quest-phase-${phase.key}`;
  const lockState = phase.locked && !justUnlocked;

  return (
    <div className={`card overflow-hidden transition-opacity ${lockState ? "opacity-65" : "opacity-100"}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Phase ${phase.index + 1}: ${phase.title.replace(/^Phase \d+\s*[—-]\s*/, "")} (${phase.locked ? "locked, preview only" : "unlocked"})`}
        className="flex w-full items-center gap-3 px-4 py-3 text-left sm:px-5"
      >
        <span aria-hidden className="text-ink-300 transition-transform" style={{ transform: open ? "rotate(90deg)" : "none" }}>
          ▸
        </span>
        <div className="min-w-0 flex-1">
          {/* motion-independent, ungated by the 1.4s badge teardown — announces the unlock to AT */}
          <span className="sr-only" role="status" aria-live="polite">
            {justUnlocked ? `Phase ${phase.index + 1} unlocked — now playable` : ""}
          </span>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="label-mono !text-[10px]">Phase {phase.index + 1}</span>
            <span className="truncate font-semibold">{phase.title.replace(/^Phase \d+\s*[—-]\s*/, "")}</span>
            {lockState && (
              <span className="inline-flex items-center gap-1 rounded-full bg-ink/[0.06] px-2 py-0.5 text-[10px] text-ink-300">
                🔒 Locked
              </span>
            )}
            {justUnlocked && (
              <m.span
                className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300"
                initial={reduced ? { opacity: 0 } : { scale: 0.6, opacity: 0 }}
                animate={reduced ? { opacity: 1 } : { scale: [0.6, 1.15, 1], opacity: 1 }}
                transition={{ duration: 0.6 }}
              >
                🔓 Unlocked!
              </m.span>
            )}
          </div>
          {/* progress bar — always visible */}
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink/[0.08]"
              role="progressbar"
              aria-valuenow={phase.pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuetext={`${phase.completed} of ${phase.total} tasks (${phase.pct}%)`}
              aria-label={`Phase ${phase.index + 1} progress`}
            >
              <span
                className="block h-full rounded-full transition-[width] duration-500 ease-out"
                style={{ width: `${phase.pct}%`, background: `linear-gradient(90deg, ${theme.nodeAccent}, ${theme.pathDoneColor})` }}
              />
            </span>
            <span className="shrink-0 font-mono text-[11px] text-ink-300">
              {phase.completed}/{phase.total}
            </span>
          </div>
        </div>
        {/* coin total for this phase */}
        <span className="flex shrink-0 items-center gap-1 text-sm">
          <span className="flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-stone-900" style={{ background: theme.coinColor }}>
            ¢
          </span>
          <Tally value={phase.coinsEarned} className="font-semibold tabular-nums" />
        </span>
      </button>

      {open && (
        <div id={panelId} className="border-t border-ink/[0.06] px-3 pb-4 pt-1 sm:px-4">
          <p className="px-1 pt-2 text-xs text-ink-500">{phase.blurb}</p>
          <PhaseMap
            phase={phase}
            theme={theme}
            shareUrl={shareUrl}
            onStatus={onStatus}
            onPhaseComplete={onPhaseComplete}
            visible={visible}
          />
        </div>
      )}
    </div>
  );
}
