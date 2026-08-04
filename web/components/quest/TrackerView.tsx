"use client";

// The implementation tracker behind BOTH results tabs. One shared QuestTracker instance
// (owned here) feeds two facets, so progress, verify results, and the share link can never
// disagree between them:
//   • facet "map"      → the Roadmap tab: the gamified Quest Map, nothing else.
//   • facet "strategy" → the Strategy tab: the merged actionable list — tracked steps in
//     roadmap phase order (Quick Wins → Foundation → Growth & Scale) with the automatic
//     site-check crawler, the deduped strategic "big moves" from the audit, and the
//     Developer handoff section as its own separate element.
// Both facets stay mounted once opened and toggle via `hidden`, so switching tabs never
// drops presentation state (open phases, expanded how-tos) or re-syncs milestones.

import { useState } from "react";

import { DeveloperHandoffPanel, MilestoneDashboard } from "../MilestoneDashboard";
import type { PackPreview, StructuredPlan } from "@/lib/types";
import { PackPlanSection } from "./PackPlanSection";
import { QuestMap } from "./QuestMap";
import { useQuestTracker } from "./useQuestTracker";

export type TrackerFacet = "map" | "strategy";

/** The pack context "Your plan" needs to fold a pack's fixes in (Phase 3 item 3.4). Absent
 *  on the no-domain / brief-only path, which has no run and therefore no packs. */
export interface PackPlanContext {
  runId: number;
  packs: PackPreview[];
  selectedPack: number | null;
  onSelectPack: (packIndex: number) => void;
  onUnlock: (packIndex: number) => void;
}

export function TrackerView({
  domain,
  plan,
  businessName,
  cmsType,
  facet,
  packContext,
  visible = true,
}: {
  domain: string;
  plan: StructuredPlan;
  businessName: string;
  cmsType?: string | null;
  /** Which results tab is hosting the tracker right now (Roadmap = map, Strategy = list). */
  facet: TrackerFacet;
  /** Pack fixes to fold into this plan (item 3.4). Omitted on the no-domain path. */
  packContext?: PackPlanContext;
  // False while the tracker is mounted but hidden behind another results tab — the map
  // defers its celebrations and "opened" analytics until it can actually be seen.
  visible?: boolean;
}) {
  // The single tracker instance both facets render — syncs once, then every status change,
  // verify, or link rotation from either facet lands in the same state.
  const tracker = useQuestTracker({ domain, plan, businessName, cmsType });

  // Track which facets have ever been shown; mount lazily, then keep mounted behind
  // `hidden` so tab switches preserve state without re-firing effects.
  const [mounted, setMounted] = useState<Set<TrackerFacet>>(() => new Set<TrackerFacet>([facet]));
  if (!mounted.has(facet)) setMounted(new Set(mounted).add(facet));

  // "Bigger strategic moves" used to render here, below the tracked list. It now lives in the
  // Overview tab (Phase 3 item 3.3) — it is orientation, not a step you work, so it belongs
  // beside the score rather than interrupting the do-this-next list. The dedupe against the
  // plan still runs, once, in ResultsView; it just has one consumer now instead of two.
  return (
    <div>
      {mounted.has("map") && (
        <div hidden={facet !== "map"}>
          <QuestMap domain={domain} tracker={tracker} visible={visible && facet === "map"} />
        </div>
      )}
      {mounted.has("strategy") && (
        <div hidden={facet !== "strategy"} className="space-y-6">
          <p className="text-sm text-ink-500">
            Every step to take, in the order that pays off fastest — Quick Wins first. Check
            things off yourself, or publish the change and let the automatic site check verify
            it for you.
          </p>
          <MilestoneDashboard tracker={tracker} />
          {/* item 3.4 — the selected pack's fixes, in the same phases and the same card
              chrome as the list above. Deliberately BESIDE the tracker, not inside it:
              pack tickets are a different server-side family, and mounting a second tracker
              is what this file's header comment forbids. The tracker keeps ownership of
              progress, "Check my site now" and the share link, which this section reuses. */}
          {packContext && (
            <PackPlanSection
              runId={packContext.runId}
              packs={packContext.packs}
              selectedPack={packContext.selectedPack}
              onSelectPack={packContext.onSelectPack}
              onUnlock={packContext.onUnlock}
              shareUrl={tracker.shareUrl}
            />
          )}
          <DeveloperHandoffPanel tracker={tracker} />
        </div>
      )}
    </div>
  );
}
