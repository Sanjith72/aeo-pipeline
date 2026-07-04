"use client";

// The implementation tracker with a List ⇄ Map toggle. Both views render ONE shared
// QuestTracker instance (owned here), so progress, verify results, and the share link can
// never disagree between them; the Map (Quest) is the gamified presentation, the List is
// the plain, dense view. Defaults to the Map so the journey leads, with List one click away.

import { useState } from "react";

import { api } from "@/lib/api";
import { MilestoneDashboard } from "../MilestoneDashboard";
import type { StructuredPlan } from "@/lib/types";
import { QuestMap } from "./QuestMap";
import { useQuestTracker } from "./useQuestTracker";

type View = "map" | "list";

const TABS: { id: View; label: string }[] = [
  { id: "map", label: "🗺️ Quest map" },
  { id: "list", label: "☰ List" },
];

export function TrackerView({
  domain,
  plan,
  businessName,
  cmsType,
  visible = true,
}: {
  domain: string;
  plan: StructuredPlan;
  businessName: string;
  cmsType?: string | null;
  // False while the tracker is mounted but hidden behind another results tab — the map
  // defers its celebrations and "opened" analytics until it can actually be seen.
  visible?: boolean;
}) {
  // The single tracker instance both views render — syncs once, then every status change,
  // verify, or link rotation from either view lands in the same state.
  const tracker = useQuestTracker({ domain, plan, businessName, cmsType });
  const [view, setView] = useState<View>("map");
  // Track which views have ever been shown. Both stay mounted once opened and toggle via
  // CSS, so switching never drops presentation state (open phases, expanded how-tos) — the
  // inactive view simply waits behind `hidden`.
  const [mounted, setMounted] = useState<Set<View>>(() => new Set<View>(["map"]));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold">Your implementation roadmap</h3>
        <div
          role="tablist"
          aria-label="Tracker view"
          className="flex gap-1 rounded-lg border border-ink/10 bg-paper-200/60 p-1 text-[12px]"
        >
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={view === id}
              onClick={() => {
                if (view === id) return;
                setView(id);
                setMounted((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
                api.track("tracker_view_switch", { view: id });
              }}
              className={`rounded-md px-3 py-1.5 transition-all duration-200 ${
                view === id ? "bg-paper-100 font-medium text-ink shadow-card" : "text-ink-300 hover:text-ink-500"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {mounted.has("map") && (
        <div hidden={view !== "map"}>
          <QuestMap domain={domain} tracker={tracker} visible={visible && view === "map"} />
        </div>
      )}
      {mounted.has("list") && (
        <div hidden={view !== "list"}>
          <MilestoneDashboard tracker={tracker} />
        </div>
      )}
    </div>
  );
}
