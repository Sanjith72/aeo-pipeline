"use client";

// The checkpoint celebration — a centered modal that fires when banked coins cross a 25%
// milestone of the plan's total (25/50/75/100), bigger than the per-phase Confetti but cut
// from the same motion cloth. One-shot: the parent (QuestMap) decides when a checkpoint is
// crossed; this component only celebrates it, auto-dismisses, and hands focus back.
// Reduced motion renders NO modal at all — the header Tally still ticks, so the reward
// isn't lost — and immediately reports done so the parent's state doesn't dangle.

import { useEffect, useRef } from "react";

import { AnimatePresence, m, useReducedMotion } from "@/components/motion/primitives";

export interface Checkpoint {
  pct: 25 | 50 | 75 | 100;
  coins: number;
}

const HEADLINES: Record<Checkpoint["pct"], { glyph: string; title: string }> = {
  25: { glyph: "🥉", title: "Quarter conquered — 25%" },
  50: { glyph: "🥈", title: "Halfway there — 50%" },
  75: { glyph: "🥇", title: "Home stretch — 75%" },
  100: { glyph: "🏆", title: "Quest complete — 100%" },
};

// On-palette celebration pieces: the site's whites/greys plus the "banked" emerald.
const PIECES = ["#ffffff", "#d4d4d8", "#10b981", "#e5e5e5", "#34d399", "#a1a1aa"];

export function CheckpointModal({
  checkpoint,
  onDone,
}: {
  checkpoint: Checkpoint | null;
  onDone: () => void;
}) {
  const reduced = useReducedMotion();
  const dismissRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Auto-dismiss, Escape-to-close, focus containment, and focus hand-off. Under reduced
  // motion nothing renders, so resolve the checkpoint immediately instead of leaving the
  // parent latched open.
  useEffect(() => {
    if (!checkpoint) return;
    if (reduced) {
      onDone();
      return;
    }
    const opener = document.activeElement as HTMLElement | null;
    dismissRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onDone();
        return;
      }
      // aria-modal promises an inert background; the dismiss button is deliberately the
      // dialog's only control, so Tab in either direction stays on it.
      if (e.key === "Tab") {
        e.preventDefault();
        dismissRef.current?.focus();
      }
    };
    // Focus can also leave programmatically: a mark made from TaskDetailPanel can cross a
    // milestone, and that panel's exit-restore fires ~280ms after this modal opens, yanking
    // focus to a node behind the backdrop. Any focus landing outside comes straight back.
    const onFocusIn = (e: FocusEvent) => {
      const root = dialogRef.current;
      if (root && e.target instanceof Node && !root.contains(e.target)) {
        dismissRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("focusin", onFocusIn);
    const t = setTimeout(onDone, 5200);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("focusin", onFocusIn);
      clearTimeout(t);
      // The opener may already be unmounted (it can be the detail panel's button when a mark
      // from the panel triggered this crossing) — focusing a detached node is a silent no-op,
      // so only restore when it's still in the document.
      if (opener?.isConnected) opener.focus?.();
    };
  }, [checkpoint, reduced, onDone]);

  const head = checkpoint ? HEADLINES[checkpoint.pct] : null;

  return (
    <AnimatePresence>
      {checkpoint && head && !reduced && (
        <m.div
          key={checkpoint.pct}
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onDone}
        >
          {/* full-screen celebration rain behind the card */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
            {Array.from({ length: 28 }, (_, i) => (
              <m.span
                key={`${checkpoint.pct}-${i}`}
                className="absolute top-0 h-2 w-2 rounded-sm"
                style={{ left: `${4 + (i * 92) / 28}%`, background: PIECES[i % PIECES.length] }}
                initial={{ y: -24, opacity: 1, rotate: 0 }}
                animate={{ y: "100vh", opacity: [1, 1, 0], rotate: 360 + i * 20 }}
                transition={{ duration: 1.7 + (i % 5) * 0.2, ease: "easeIn", delay: (i % 7) * 0.05 }}
              />
            ))}
          </div>

          <m.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label={head.title}
            className="card relative w-full max-w-sm px-6 py-8 text-center"
            initial={{ scale: 0.7, y: 16, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
            transition={{ type: "spring", stiffness: 220, damping: 16 }}
            onClick={(e) => e.stopPropagation()}
          >
            <m.p
              className="text-5xl leading-none"
              aria-hidden
              initial={{ scale: 0.4, rotate: -12 }}
              animate={{ scale: [0.4, 1.25, 1], rotate: [0, 6, 0] }}
              transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: 0.1 }}
            >
              {head.glyph}
            </m.p>
            <p className="mt-3 text-xl font-bold text-ink">{head.title}</p>
            <p className="mt-1.5 text-sm text-ink-500">
              <span className="font-semibold tabular-nums text-emerald-300">{checkpoint.coins}</span>{" "}
              coins banked
            </p>
            <button
              ref={dismissRef}
              type="button"
              onClick={onDone}
              className="btn-primary mt-5 !py-2 text-[13px]"
            >
              Keep going
            </button>
          </m.div>
        </m.div>
      )}
    </AnimatePresence>
  );
}
