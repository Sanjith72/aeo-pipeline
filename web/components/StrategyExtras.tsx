"use client";

// The strategic "big moves" from the site audit (profile.actions) that the tracked plan
// doesn't already cover as a concrete task — the surviving half of the old Roadmap-tab
// list after the Roadmap↔Strategy merge (lib/phases.dedupeActionsAgainstPlan). Grouped in
// roadmap phase order (Quick Wins → Foundation → Growth & Scale) below the tracked list,
// so nothing from the audit is lost but nothing is listed twice.

import { EFFORT_LABEL, humanizeToken } from "@/lib/options";
import { groupActionsByPhase, phaseDisplayTitle } from "@/lib/phases";
import type { StrategyAction } from "@/lib/types";

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
  medium: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
  high: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30",
};

export function StrategyExtras({ actions }: { actions: StrategyAction[] }) {
  const groups = groupActionsByPhase(actions);
  if (groups.length === 0) return null;
  return (
    <div className="card p-5 sm:p-6">
      <h3 className="text-base font-semibold">Bigger strategic moves</h3>
      <p className="mt-0.5 max-w-2xl text-sm text-ink-500">
        From your site audit — direction-setting moves that go beyond a single tracked change.
        They follow the same phase order as your plan above.
      </p>
      <div className="mt-4 space-y-5">
        {groups.map(({ key, actions: items }) => (
          <div key={key}>
            <span className="label-mono">{phaseDisplayTitle(key, humanizeToken(key))}</span>
            <ul className="mt-2 space-y-2">
              {items.map((a) => (
                <li
                  key={`${a.priority}-${a.title}`}
                  className="rounded-lg border border-ink/[0.06] bg-paper-200/30 p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{a.title}</span>
                    <span className="label-mono rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">
                      {humanizeToken(a.category)}
                    </span>
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${EFFORT_PILL[a.effort] ?? "bg-ink/5 text-ink-500"}`}>
                      {EFFORT_LABEL[a.effort] ?? humanizeToken(a.effort)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm leading-relaxed text-ink-500">{a.detail}</p>
                  {a.related_slugs.length > 0 && (
                    <p className="mt-1.5 font-mono text-xs text-ink-300">pages: {a.related_slugs.join("  ·  ")}</p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
