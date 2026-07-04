"use client";

// Data ownership for the implementation tracker — the single source of truth behind BOTH
// presentations (the gamified Quest Map and the plain List). One instance lives in
// TrackerView and is handed to both views, so a status change, "check my site" verify, or
// share-link rotation made in either view is reflected in the other immediately — no
// duplicate syncs, no drift between the two.

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { buildQuestModel } from "@/lib/quest/model";
import type { QuestModel } from "@/lib/quest/types";
import type { MilestoneDashboard, MilestoneStatus, StructuredPlan } from "@/lib/types";

// Optimistic local patch: flip one task's status and re-derive the roll-up so both views
// react instantly before the server's recomputed dashboard arrives.
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
  /** Raw server dashboard — what the List view renders. */
  dash: MilestoneDashboard | null;
  /** The dashboard reshaped into phases/enemies — what the Quest Map renders. */
  model: QuestModel | null;
  shareUrl: string | null;
  error: string | null;
  verifying: boolean;
  /** Outcome of the last "Check my site now" — each view words its own note from the count. */
  lastVerify: { newlyVerified: number } | null;
  rotating: boolean;
  setStatus: (taskKey: string, status: MilestoneStatus) => Promise<void>;
  checkSite: () => Promise<void>;
  /** Revoke the current share link and mint a fresh token (no confirm here — views own that). */
  rotateShareLink: () => Promise<void>;
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
  const [lastVerify, setLastVerify] = useState<{ newlyVerified: number } | null>(null);
  const [rotating, setRotating] = useState(false);

  // Persist (sync) the generated plan as milestones on mount, then render what comes back.
  // Idempotent server-side, so this is safe on every revisit — existing progress and
  // crawl-verified status are preserved.
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

  // Analytics note: the views fire their own status events (quest_task_status /
  // milestone_task_status) so telemetry keeps telling the two surfaces apart.
  const setStatus = useCallback(
    async (taskKey: string, status: MilestoneStatus) => {
      setDash((prev) => (prev ? patchTask(prev, taskKey, status) : prev));
      try {
        setDash(await api.setMilestoneTask({ domain, task_key: taskKey, status }));
        // Success clears any earlier failure — the error is shared by both views, so a
        // stale banner must not linger on a surface the user visits later.
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [domain],
  );

  const checkSite = useCallback(async () => {
    setVerifying(true);
    setLastVerify(null);
    try {
      const res = await api.verifyMilestones(domain);
      setDash(res.dashboard);
      setLastVerify({ newlyVerified: res.summary.newly_verified });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifying(false);
    }
  }, [domain]);

  // Updating share_token on the dashboard re-derives shareUrl, which flows down to every
  // task expander in both views — all mailto: links and textareas rebuild automatically.
  const rotateShareLink = useCallback(async () => {
    setRotating(true);
    setError(null);
    try {
      const { share_token } = await api.rotateShareLink(domain);
      setDash((prev) => (prev ? { ...prev, share_token } : prev));
      api.track("dev_handoff_link_rotated", {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRotating(false);
    }
  }, [domain]);

  const model = useMemo(() => (dash ? buildQuestModel(plan, dash) : null), [plan, dash]);
  const shareUrl =
    dash?.share_token && typeof window !== "undefined"
      ? `${window.location.origin}/share/${dash.share_token}`
      : null;

  return { dash, model, shareUrl, error, verifying, lastVerify, rotating, setStatus, checkSite, rotateShareLink };
}
