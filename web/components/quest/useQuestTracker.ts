"use client";

// Data ownership for the Quest Map — a thin, self-contained mirror of MilestoneDashboard's
// pattern (sync on mount, optimistic status writes, manual "check my site" verify). Kept
// separate so the shipped List view stays untouched; the server is the single source of
// truth, so both views stay consistent across the List ⇄ Map toggle.

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { buildQuestModel } from "@/lib/quest/model";
import type { QuestModel } from "@/lib/quest/types";
import type { MilestoneDashboard, MilestoneStatus, StructuredPlan } from "@/lib/types";

// Optimistic local patch (mirrors MilestoneDashboard.patchTask): flip one task's status and
// re-derive the roll-up so the map reacts instantly before the server's recomputed dashboard.
function patchTask(dash: MilestoneDashboard, taskKey: string, status: MilestoneStatus): MilestoneDashboard {
  const milestones = dash.milestones.map((m) => ({
    ...m,
    tasks: m.tasks.map((t) => (t.task_key === taskKey ? { ...t, status, status_source: "manual" as const } : t)),
  }));
  const all = milestones.flatMap((m) => m.tasks);
  const verified = all.filter((t) => t.status === "verified_completed").length;
  const inProgress = all.filter((t) => t.status === "in_progress").length;
  const total = all.length;
  return {
    ...dash,
    milestones,
    progress: { total, verified, in_progress: inProgress, pct: total ? Math.round((verified / total) * 100) : 0 },
  };
}

export interface QuestTracker {
  model: QuestModel | null;
  shareUrl: string | null;
  error: string | null;
  verifying: boolean;
  verifyNote: string | null;
  setStatus: (taskKey: string, status: MilestoneStatus) => Promise<void>;
  checkSite: () => Promise<void>;
}

export function useQuestTracker({
  domain,
  plan,
  businessName,
  cmsType,
}: {
  domain: string;
  plan: StructuredPlan;
  businessName: string;
  cmsType?: string | null;
}): QuestTracker {
  const [dash, setDash] = useState<MilestoneDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .syncMilestones({ domain, name: businessName || undefined, plan, cms_type: cmsType ?? undefined })
      .then((d) => !cancelled && setDash(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [domain, businessName, plan, cmsType]);

  const setStatus = useCallback(
    async (taskKey: string, status: MilestoneStatus) => {
      setDash((prev) => (prev ? patchTask(prev, taskKey, status) : prev));
      api.track("quest_task_status", { task_key: taskKey, status });
      try {
        setDash(await api.setMilestoneTask({ domain, task_key: taskKey, status }));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [domain],
  );

  const checkSite = useCallback(async () => {
    setVerifying(true);
    setVerifyNote(null);
    try {
      const res = await api.verifyMilestones(domain);
      setDash(res.dashboard);
      const n = res.summary.newly_verified;
      setVerifyNote(
        n > 0
          ? `Verified live — ${n} change${n === 1 ? "" : "s"} confirmed on your site. Bonus coins awarded.`
          : "We checked your site — nothing new is live yet. Publish a change, then check again.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifying(false);
    }
  }, [domain]);

  const model = useMemo(() => (dash ? buildQuestModel(plan, dash) : null), [plan, dash]);
  const shareUrl =
    dash?.share_token && typeof window !== "undefined"
      ? `${window.location.origin}/share/${dash.share_token}`
      : null;

  return { model, shareUrl, error, verifying, verifyNote, setStatus, checkSite };
}
