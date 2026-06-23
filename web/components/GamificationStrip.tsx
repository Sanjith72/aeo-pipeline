// web/components/GamificationStrip.tsx
"use client";

import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { MATURITY_LABEL, type MaturityStage, maturityProgress } from "../lib/gamify";
import type { GamificationView } from "../lib/types";
import { CountUp, Tally } from "./motion/primitives";

/** A restrained status strip — the AEO band, verified-win count, and maturity. Reads the same
 *  verdict-backed state the engine computes; never invents progress. Renders nothing until
 *  there's real state, so a brand-new session sees no empty gamification chrome. */
export function GamificationStrip({ domain, aeoScore }: { domain?: string; aeoScore?: number }) {
  const [view, setView] = useState<GamificationView>({ state: null, awards: [] });

  useEffect(() => {
    let alive = true;
    async function load() {
      if (domain && typeof aeoScore === "number") {
        await api.reconcileGamification(domain, aeoScore).catch(() => {});
      }
      const v = await api.getGamification(domain);
      if (alive) setView(v);
    }
    void load();
    return () => {
      alive = false;
    };
  }, [domain, aeoScore]);

  const s = view.state;
  if (!s) return null;

  const stage = (s.maturity_stage as MaturityStage) ?? "foundations";
  return (
    <div className="card flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 text-sm">
      <div className="flex items-baseline gap-1.5">
        <span className="label-mono !text-[10px]">AEO</span>
        <CountUp to={s.aeo_score ?? 0} className="font-semibold text-ink" />
        <span className="text-ink-500">{s.aeo_band ?? ""}</span>
      </div>
      <span className="hidden h-4 w-px bg-ink/10 sm:block" />
      <div className="flex items-baseline gap-1.5">
        <span className="label-mono !text-[10px]">Verified wins</span>
        <Tally value={s.verified_wins} className="font-semibold text-ink" />
      </div>
      <span className="hidden h-4 w-px bg-ink/10 sm:block" />
      <div className="flex items-center gap-2">
        <span className="text-ink-500">{MATURITY_LABEL[stage]}</span>
        <span className="inline-block h-1.5 w-24 overflow-hidden rounded-full bg-ink/10">
          <span
            className="block h-full rounded-full bg-accent transition-[width] duration-500"
            style={{ width: `${Math.round(maturityProgress(stage) * 100)}%` }}
          />
        </span>
      </div>
    </div>
  );
}
