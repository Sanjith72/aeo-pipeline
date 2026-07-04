"use client";

// The Quest Map — the gamified presentation of the implementation tracker. Three phase tabs
// (Pirate / Dungeon / Space), each a themed map of enemy-nodes you defeat by completing the
// real task underneath. Renders the shared QuestTracker owned by TrackerView (the same
// instance the List view reads), so both presentations always agree; coins/enemies are
// honest projections, never invented progress.

import { useEffect, useRef, useState } from "react";

import { AnimatePresence, m, Tally, useReducedMotion } from "@/components/motion/primitives";
import { themeForPhase } from "@/lib/quest/theme";
import type { QuestPhase } from "@/lib/quest/types";
import type { MilestoneStatus } from "@/lib/types";
import { api } from "@/lib/api";
import { PhaseTab } from "./PhaseTab";
import type { QuestTracker } from "./useQuestTracker";

const CONFETTI_COLORS = ["#ffffff", "#d4d4d8", "#a1a1aa", "#e5e5e5", "#f4f4f5"];

function Confetti({ fireKey }: { fireKey: number }) {
  const reduced = useReducedMotion();
  if (reduced || fireKey === 0) return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden" aria-hidden>
      {Array.from({ length: 18 }, (_, i) => (
        <m.span
          key={`${fireKey}-${i}`}
          className="absolute top-0 h-2 w-2 rounded-sm"
          style={{ left: `${6 + (i * 88) / 18}%`, background: CONFETTI_COLORS[i % CONFETTI_COLORS.length] }}
          initial={{ y: -20, opacity: 1, rotate: 0 }}
          animate={{ y: "100vh", opacity: [1, 1, 0], rotate: 360 + i * 24 }}
          transition={{ duration: 1.5 + (i % 5) * 0.18, ease: "easeIn", delay: (i % 6) * 0.04 }}
        />
      ))}
    </div>
  );
}

export function QuestMap({
  domain,
  tracker,
  visible = true,
}: {
  domain: string;
  tracker: QuestTracker;
  // False while the map is mounted but not on screen (List toggle or another results tab).
  // Celebrations and "opened" analytics wait for the first moment it is actually visible.
  visible?: boolean;
}) {
  const { model, shareUrl, error, verifying, lastVerify, checkSite } = tracker;
  const verifyNote = lastVerify
    ? lastVerify.newlyVerified > 0
      ? `Verified live — ${lastVerify.newlyVerified} change${lastVerify.newlyVerified === 1 ? "" : "s"} confirmed on your site. Bonus coins awarded.`
      : "We checked your site — nothing new is live yet. Publish a change, then check again."
    : null;
  // Fire the quest event before the shared write so telemetry keeps naming this surface.
  const setStatus = (taskKey: string, status: MilestoneStatus) => {
    api.track("quest_task_status", { task_key: taskKey, status });
    return tracker.setStatus(taskKey, status);
  };
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [celebrateKey, setCelebrateKey] = useState(0);
  const didInit = useRef(false);

  // Default to single-open on first SHOWING: the first unlocked, not-yet-finished phase.
  // Gated on visibility so a hidden-mounted map (List toggle, other results tab) doesn't
  // claim a quest_view_opened the user never saw — it fires on first reveal instead.
  useEffect(() => {
    if (didInit.current || !visible || !model || model.phases.length === 0) return;
    const start = model.phases.find((p) => !p.locked && !p.isComplete) ?? model.phases[0];
    setOpen(new Set([start.key]));
    didInit.current = true;
    api.track("quest_view_opened", { domain });
  }, [model, domain, visible]);

  function toggle(key: string) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key); // don't force-close others — let the user compare phases
      return next;
    });
  }

  function handlePhaseComplete(phase: QuestPhase) {
    setCelebrateKey((k) => k + 1);
    api.track("quest_phase_complete", { phase: phase.key });
    // Auto-open the freshly-unlocked next phase so the journey keeps flowing.
    const next = model?.phases[phase.index + 1];
    if (next) setOpen((prev) => new Set(prev).add(next.key));
  }

  if (error && !model) {
    return (
      <div className="rounded-xl border border-rose-500/30 bg-rose-500/[0.06] p-5 text-sm text-rose-200">
        Couldn&apos;t load your quest: {error}
      </div>
    );
  }
  if (!model) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-5 text-sm text-ink-500">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
        Charting your map…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Confetti fireKey={celebrateKey} />

      {/* header: global coin bank + verify */}
      <div className="card flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-sm font-bold text-paper shadow-glow">
            ¢
          </span>
          <div className="leading-tight">
            <Tally value={model.globalCoins} className="text-lg font-bold tabular-nums text-ink" />
            <p className="label-mono !text-[9px]">coins earned</p>
          </div>
        </div>
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

      <AnimatePresence>
        {verifyNote && (
          <m.p
            className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3.5 py-2.5 text-sm text-emerald-300"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            {verifyNote}
          </m.p>
        )}
      </AnimatePresence>
      {error && model && (
        <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3.5 py-2 text-xs text-rose-300">{error}</p>
      )}

      {model.phases.map((phase) => (
        <PhaseTab
          key={phase.key}
          phase={phase}
          theme={themeForPhase(phase.key, phase.index)}
          open={open.has(phase.key)}
          onToggle={() => toggle(phase.key)}
          shareUrl={shareUrl}
          onStatus={setStatus}
          onPhaseComplete={handlePhaseComplete}
          visible={visible}
        />
      ))}
    </div>
  );
}
