"use client";

// A burst of blue coins that spawns at a defeated enemy and arcs upward toward the phase's
// progress bar/coin total at the top of the stage. Count scales with the task's impact, so a
// high-impact win visibly rains more coins. Reduced-motion renders nothing (the Tally in the
// header still ticks, so the reward is never lost). Self-removing via onDone.

import { useEffect } from "react";

import { AnimatePresence, m, useReducedMotion } from "@/components/motion/primitives";

export interface BurstSpec {
  key: number; // unique per burst so re-bursts re-trigger
  xPct: number; // origin within the stage (0–100)
  yPct: number;
  coins: number; // reward size → coin count
  color: string;
  bonus?: boolean; // banked chest-bonus burst (emerald "verified" ring)
  pending?: boolean; // coins not banked yet (amber "pending verification" ring)
}

// Map a coin reward to a sensible visible coin count (handful → big burst), capped so a huge
// task doesn't flood the stage.
function coinCount(coins: number): number {
  return Math.max(4, Math.min(16, Math.round(coins / 14)));
}

export function CoinBurst({ burst, onDone }: { burst: BurstSpec | null; onDone: () => void }) {
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!burst) return;
    const ms = reduced ? 0 : 1100;
    const t = setTimeout(onDone, ms);
    return () => clearTimeout(t);
  }, [burst, reduced, onDone]);

  if (!burst || reduced) return null;
  const n = coinCount(burst.coins);

  return (
    <div className="pointer-events-none absolute inset-0 z-30 overflow-hidden" aria-hidden>
      <AnimatePresence>
        {Array.from({ length: n }, (_, i) => {
          const spread = (i / Math.max(1, n - 1) - 0.5) * 80; // horizontal fan, in px
          const rise = 120 + (i % 4) * 34; // travel up toward the header
          return (
            <m.span
              key={`${burst.key}-${i}`}
              className="absolute flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold text-paper shadow"
              style={{
                left: `${burst.xPct}%`,
                top: `${burst.yPct}%`,
                background: burst.color,
                // bonus ring = the app's "verified" emerald; pending ring = the app's
                // "pending verification" amber — never an off-palette yellow
                boxShadow: burst.bonus
                  ? "0 0 0 2px rgba(16,185,129,0.7)"
                  : burst.pending
                    ? "0 0 0 2px rgba(251,191,36,0.65)"
                    : undefined,
              }}
              initial={{ x: "-50%", y: "-50%", opacity: 0, scale: 0.3 }}
              animate={{
                x: `calc(-50% + ${spread}px)`,
                y: [`-50%`, `calc(-50% - ${rise * 0.5}px)`, `calc(-50% - ${rise}px)`],
                opacity: [0, 1, 1, 0],
                scale: [0.3, 1, 1, 0.8],
              }}
              transition={{ duration: 0.95, ease: [0.16, 1, 0.3, 1], delay: i * 0.025 }}
            >
              ¢
            </m.span>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export { coinCount };
