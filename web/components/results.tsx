"use client";

// The results experience — one tabbed dashboard after the analysis instead of extra
// wizard steps. Every label that comes back from the API gets translated into owner
// language here. The centerpiece is the interactive, phased plan (#10/#13): work it in
// the app, check things off, with both an AI prompt and a human how-to per task.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type {
  AuditJob,
  BriefPlan,
  BundleAsset,
  DeliverablesResponse,
  PackPreview,
  PendingFix,
  PlanStateResponse,
  PlanTask,
  RecheckStatusResponse,
  SiteProfile,
  SitemapNode,
  StructuredPlan,
  VerifiedOutcome,
} from "@/lib/types";
import { DELIVERABLE_LABEL, EFFORT_LABEL, INTENT_LABEL, SCENARIO_LABEL, humanizeToken } from "@/lib/options";
import { strategyExtrasState } from "@/lib/phases";
import { aeoScore, aeoScoreCeiling, scoreBand, type ScoreTone } from "@/lib/score";
import { predictedLiftChip, reconcileLabel } from "@/lib/predictedLift";
import { CountUp, Tally, useReducedMotion } from "./motion/primitives";
import { ArrowRight, Check, Sparkle } from "./ui/icons";
import { TaskHowTo } from "./TaskHowTo";
import { StrategyExtras } from "./StrategyExtras";
import { PagesPanel } from "./PagesPanel";
import { TrackerView, type PackPlanContext, type TrackerFacet } from "./quest/TrackerView";
import { usePackTickets } from "./quest/usePackTickets";
import type { PackWork } from "./MilestoneDashboard";
import { useAuth } from "./auth/AuthProvider";
import { UnlockModal } from "./auth/UnlockModal";
import { clearPendingUnlock, readPendingUnlock, rememberPendingUnlock } from "@/lib/pendingUnlock";

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
  medium: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
  high: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30",
};

// Phase rank for sorting the "Today" tray (earliest phase first).
const PHASE_RANK: Record<string, number> = { week_1: 0, week_2_4: 1, later: 2 };

type TabId = "overview" | "blueprint" | "actions" | "strategy" | "pages" | "kit";

// ── canonical AEO score ring (Spec #1) ──────────────────────────────────────────

const RING_TONE: Record<ScoreTone, { stroke: string; text: string; soft: string }> = {
  rose: { stroke: "stroke-rose-400", text: "text-rose-300", soft: "text-rose-300/30" },
  amber: { stroke: "stroke-amber-400", text: "text-amber-200", soft: "text-amber-300/30" },
  sky: { stroke: "stroke-sky-400", text: "text-sky-300", soft: "text-sky-300/30" },
  emerald: { stroke: "stroke-emerald-400", text: "text-emerald-300", soft: "text-emerald-300/30" },
};

/** The canonical AEO Score as a gauge: a solid arc for where the site is today and a
 *  ghosted arc for where finishing the plan gets it. The number only really moves on a
 *  re-audit — the plan's progress bar handles task-by-task feedback — so the ring stays
 *  honest (no self-graded climbing; that's the re-crawl-verified Spec #2).
 *
 *  `provisional` (Critical #1): the same honest score, computed from the *fast* homepage
 *  crawl and shown the instant it lands (step 1) — the credit-score/speed-test moment that
 *  converts skepticism into "show me how", long before the 5–15 min deep audit. aeoScore()
 *  runs on the fast and the deep profile alike (see lib/score.ts), so the number refines
 *  rather than contradicting itself — we just label it as an early read. */
export function ScoreRing({
  profile,
  className,
  provisional = false,
}: {
  profile: SiteProfile;
  className?: string;
  provisional?: boolean;
}) {
  const score = aeoScore(profile);
  const ceiling = aeoScoreCeiling(profile);
  const band = scoreBand(score);
  const tone = RING_TONE[band.tone];

  const R = 52;
  const C = 2 * Math.PI * R;
  const arc = (pct: number) => `${(C * pct) / 100} ${C}`;

  return (
    <div className={`card flex flex-col items-center gap-5 p-6 sm:flex-row sm:p-7 ${className ?? ""}`}>
      <div className="relative h-32 w-32 shrink-0">
        <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90">
          <circle cx="60" cy="60" r={R} fill="none" strokeWidth="10" className="stroke-ink/[0.07]" />
          {/* ghosted target: where the plan gets you */}
          <circle
            cx="60"
            cy="60"
            r={R}
            fill="none"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={arc(ceiling)}
            className={tone.soft}
            stroke="currentColor"
          />
          {/* current score */}
          <circle
            cx="60"
            cy="60"
            r={R}
            fill="none"
            strokeWidth="10"
            strokeLinecap="round"
            strokeDasharray={arc(score)}
            className={`${tone.stroke} transition-[stroke-dasharray] duration-700 ease-out`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <CountUp to={score} className={`font-display text-3xl font-semibold ${tone.text}`} />
          <span className="label-mono mt-0.5 text-[10px]">/ 100</span>
        </div>
      </div>

      <div className="text-center sm:text-left">
        <span className="label-mono inline-flex flex-wrap items-center gap-2">
          {provisional ? "Your score — first look" : "Your AI visibility score"}
          {provisional && (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium normal-case tracking-normal text-accent ring-1 ring-accent/30">
              Provisional
            </span>
          )}
        </span>
        <h3 className={`mt-1 text-xl font-semibold ${tone.text}`}>{band.label}</h3>
        <p className="mt-1 max-w-md text-sm text-ink-500">{band.verdict}</p>
        {provisional ? (
          <p className="mt-2 max-w-md text-xs text-ink-300">
            An early read from a quick look at your homepage{ceiling > score ? <> — with a ceiling of <span className="font-medium text-ink-500">{ceiling}</span> once your plan is done</> : null}. The full
            page-by-page review checks every page to confirm it — the number stays honest either way.
          </p>
        ) : (
          ceiling > score && (
            <p className="mt-2 text-xs text-ink-300">
              Finish your plan to reach <span className="font-medium text-ink-500">{ceiling}</span> — that's the ghosted
              ring.
            </p>
          )
        )}
      </div>
    </div>
  );
}

// Feature #2 "Fix impact": pending fixes carry a PREDICTED "+X pts" lift (so the owner can
// pick high-impact work before acting); re-crawl-verified fixes (Spec #2) show predicted vs
// actual (so the estimate stays honest). Both arrive from /api/recheck-status; either may be
// empty, and the whole panel disappears when there's nothing to show.
function FixImpact({ data }: { data: RecheckStatusResponse }) {
  const pending = data.pending ?? [];
  const verified = data.verified ?? [];
  if (pending.length === 0 && verified.length === 0) return null;
  return (
    <div className="mb-6 space-y-3">
      {pending.length > 0 && <PredictedFixes fixes={pending} />}
      {verified.length > 0 && <VerifiedLive verified={verified} />}
    </div>
  );
}

// The "before you act" half: the highest-predicted-lift fixes still to do. The chip is "+X
// pts" only for a real simulated estimate; an advisory we can't simulate shows "—", never a
// fabricated 0 (see lib/predictedLift).
function PredictedFixes({ fixes }: { fixes: PendingFix[] }) {
  return (
    <div className="rounded-xl border border-accent/30 bg-accent/[0.05] p-5">
      <div className="mb-2 flex items-center gap-2 text-accent">
        <Sparkle width={14} height={14} />
        <span className="text-sm font-medium">Biggest wins left — estimated score lift before you act</span>
      </div>
      <ul className="space-y-1.5 text-xs text-ink-500">
        {fixes.slice(0, 6).map((f, i) => {
          const chip = predictedLiftChip(f.predicted);
          return (
            <li key={`${f.url}-${f.criterion ?? "x"}-${i}`} className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate">
                {f.criterion ? <span className="text-ink-300">{humanizeToken(f.criterion)} — </span> : null}
                {f.action_required || f.url}
              </span>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums ${
                  chip.known
                    ? "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30"
                    : "bg-ink/[0.05] text-ink-400 ring-1 ring-ink/10"
                }`}
                title={
                  chip.band
                    ? `Estimated ${chip.band} points on this page's rubric`
                    : chip.known
                      ? undefined
                      : "No deterministic estimate for this fix yet"
                }
              >
                {chip.label}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-[11px] text-ink-300">
        An estimate from simulating each fix on the page's 0–50 rubric — we confirm the real gain on the next re-crawl.
      </p>
    </div>
  );
}

// Spec #2 "Verified live": fixes a re-crawl has confirmed actually landed (criterion-honest),
// now with predicted vs actual where both are known.
function VerifiedLive({ verified }: { verified: VerifiedOutcome[] }) {
  return (
    <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.07] p-5">
      <div className="mb-2 flex items-center gap-2 text-emerald-300">
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
          <Check width={10} height={10} />
        </span>
        <span className="text-sm font-medium">
          Verified live — {verified.length} fix{verified.length === 1 ? "" : "es"} confirmed by a re-crawl
        </span>
      </div>
      <ul className="space-y-1 text-xs text-ink-400">
        {verified.slice(0, 6).map((v, i) => {
          const reconcile = reconcileLabel(v);
          return (
            <li key={`${v.url}-${i}`} className="flex items-center justify-between gap-3">
              <span className="min-w-0 truncate">
                <span className="text-emerald-300/80">✓</span> {v.criterion ? `${humanizeToken(v.criterion)} — ` : ""}
                {v.url}
              </span>
              {reconcile && (
                <span className="shrink-0 font-mono text-[11px] tabular-nums text-emerald-300/80">{reconcile}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// Resume context (Spec #1): present when this view is hydrating a saved /plan/<id> state,
// so the no-domain checklist seeds from and mirrors to the persisted plan_state.
export interface ResumeContext {
  planStateId: string;
  initialDone: string[];
  score: number | null;
}

export function ResultsView({
  businessName,
  domain,
  profile,
  plan,
  auditJob,
  deliverables,
  delivLoading,
  delivError,
  aiPersonalization,
  cmsType,
  packContext,
  onGenerateDeliverables,
  onPersonalize,
  personalizing,
  personalizeError,
  personalizeProgress,
  onDownloadZip,
  onEdit,
  resume,
  resumed = false,
  tabRequest,
}: {
  businessName: string;
  domain?: string;
  profile: SiteProfile | null;
  plan: BriefPlan | null;
  auditJob: AuditJob | null;
  deliverables: DeliverablesResponse | null;
  delivLoading: boolean;
  delivError: string | null;
  aiPersonalization: boolean;
  // Detected CMS, threaded down to the milestone dashboard's "I'll do it myself" steps.
  cmsType?: string | null;
  /** Pack fixes to fold into "Your plan" (Phase 3 item 3.4). Supplied by StudioApp,
   *  which owns the run and the pack grid; absent on the brief-only path. */
  packContext?: PackPlanContext;
  onGenerateDeliverables: () => void;
  onPersonalize: () => void;
  personalizing: boolean;
  personalizeError: string | null;
  personalizeProgress: string | null;
  onDownloadZip: () => void;
  onEdit: () => void;
  // Saved-plan hydration (/plan/<id>): identical view, seeded from the persisted state.
  resume?: ResumeContext;
  // Tweaks the header copy for the resumed entry ("Your saved plan" instead of
  // "Analysis complete") — the tabs and panels below are exactly the build-flow ones.
  resumed?: boolean;
  /** A one-shot ask to open a tab from OUTSIDE (a redeemed unlock landing on Pages, a
   *  just-unlocked pack). The nonce makes each ask distinct; an ask for a tab that is not
   *  available yet (packs still loading) stays live and is honoured the moment the tab
   *  exists, because arriving before the data is precisely how these asks happen. */
  tabRequest?: { id: TabId; nonce: number } | null;
}) {
  const tabs: { id: TabId; label: string }[] = [
    ...(profile ? [{ id: "overview" as const, label: "Overview" }] : []),
    ...(plan ? [{ id: "blueprint" as const, label: "Your website plan" }] : []),
    // v5 CH-10: Roadmap + Strategy are now ONE strategy-driven section ("Your plan") —
    // the merged, priority-ordered actionable list (tracked steps, automatic site-check,
    // developer handoff). The old separate gamified-quest "Roadmap" tab is retired here so
    // the plan reads as one section, not two views of it (the quest map stays in the code
    // for a later retention slice; leading with the actionable list also honors CH-11f).
    ...(profile ? [{ id: "strategy" as const, label: "Your plan" }] : []),
    // item 3.5 — page-by-page scores, their own tab rather than a wall under the pack
    // grid. Only offered when there is actually a pack to show pages for.
    ...(packContext ? [{ id: "pages" as const, label: "Pages" }] : []),
    // Bare-plan fallback (no profile → no Overview/Strategy tabs) keeps the plan reachable.
    ...(profile ? [] : [{ id: "kit" as const, label: "Your plan" }]),
  ];
  // Land on the first available tab: Overview when there's a profile, otherwise the first
  // no-profile tab (the blueprint, in practice).
  const [tab, setTab] = useState<TabId>(tabs[0]?.id ?? "kit");

  // Honour an outside ask to open a tab (see the prop's comment). Keyed on the tab SET as
  // well as the nonce: the Pages tab appears only once packs load, and an ask that raced
  // that load must win the race's replay, not be dropped.
  const tabIds = tabs.map((t) => t.id).join(",");
  const consumedTabAsk = useRef(0);
  useEffect(() => {
    if (!tabRequest || tabRequest.nonce === consumedTabAsk.current) return;
    if (!tabIds.split(",").includes(tabRequest.id)) return;
    consumedTabAsk.current = tabRequest.nonce;
    setTab(tabRequest.id);
  }, [tabRequest, tabIds]);

  // Spec #2 "Verified live": a re-crawl can confirm a recommended fix actually landed
  // (criterion-honest). Surface any confirmed-implemented outcomes for this domain in the
  // overview. Best-effort — the API resolves to an empty set on any miss, so this never
  // breaks the results view.
  /** The tracker's share link, lifted out of TrackerView so the Pages tab's fix rows can put
   *  it in their developer handoff. TrackerView owns the ONE useQuestTracker instance; a
   *  second one in PagesPanel would fire a second /api/milestones sync. */
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [recheck, setRecheck] = useState<RecheckStatusResponse>({ verified: [], pending: [], count: 0 });
  useEffect(() => {
    if (profile?.domain) api.recheckStatus(profile.domain).then(setRecheck).catch(() => {});
  }, [profile?.domain]);

  // v5 / Phase 3 item 3.3 — "Bigger strategic moves" is derived HERE, once, and rendered in
  // the Overview tab. It used to render in two places (the strategy facet of TrackerView and
  // PlanPanel's no-domain fallback), each with its own dedupe call; one derivation means the
  // two paths cannot drift, and nothing can appear in both Overview and Your plan.
  const extras = useMemo(
    () => strategyExtrasState(profile?.actions ?? [], deliverables?.plan),
    [profile?.actions, deliverables?.plan],
  );

  // `openFixInPlan` lived here: a cross-tab jump that switched to Your plan, waited two
  // animation frames for the hidden panel to lay out, scrolled to the row and flashed it.
  // It is gone because the thing it jumped TO is gone — the fixes are workable in the Pages
  // tab now, so Pages is the destination rather than a signpost to one. packFixDomId still
  // anchors each row, so a deep link remains possible without the machinery.

  // #6 — the old "reserve the tallest panel height" floor was removed: it left a large dead
  // space below shorter tabs (most visibly "Your plan" before a plan is built). The sticky
  // tab bar keeps the user oriented across switches, so panels now simply size to their own
  // content and no empty gap remains.

  // The plan/tracker — one always-mounted PlanPanel serving two tabs as facets: the
  // Roadmap tab shows the gamified quest map, the Strategy tab the merged actionable list
  // (plus the no-profile "Your plan" fallback). Mounted once so the plan keeps auto-building
  // on load even while another tab is open, and `visible` lets the subtree defer
  // celebrations and "viewed" analytics until the user can actually see it.
  const planVisible = tab === "strategy" || tab === "kit";
  // v5 CH-10: one consolidated plan section — always the strategy-driven actionable list.
  const planFacet: TrackerFacet = "strategy";

  // THE one owner of the selected pack's pages/tickets/verify-poll, rendered by BOTH the
  // Pages tab and Your plan's phase cards. Instantiated here for the same reason shareUrl
  // is lifted here: state two tabs render must have one owner, or a fix marked done on one
  // tab stays undone on the other until a poll happens to fire. The hook no-ops on null
  // (the no-domain path has no packs).
  const packTickets = usePackTickets(packContext?.runId ?? null, packContext?.selectedPack ?? null);
  const packWork: PackWork | undefined = packContext
    ? {
        packs: packContext.packs,
        selectedPack: packContext.selectedPack,
        onSelectPack: packContext.onSelectPack,
        onUnlock: packContext.onUnlock,
        state: packTickets,
      }
    : undefined;

  const planPanel = (
    <PlanPanel
      visible={planVisible}
      facet={planFacet}
      deliverables={deliverables}
      loading={delivLoading}
      slowMode={aiPersonalization}
      domain={domain?.trim() || undefined}
      businessName={businessName}
      cmsType={cmsType}
      onShareUrl={setShareUrl}
      error={delivError}
      // Salt the local key with the domain so two plans that share a (possibly derived)
      // business name — e.g. a consultant's back-to-back no-site briefs — never load each
      // other's task checkoffs from one shared "aeo-plan:" key.
      storageKey={
        resume
          ? `aeo-plan:resumed:${resume.planStateId}`
          : `aeo-plan:${(domain?.trim() || businessName || "default").toLowerCase()}`
      }
      packWork={packWork}
      resume={resume}
      onGenerate={onGenerateDeliverables}
      onDownloadZip={onDownloadZip}
      onPersonalize={onPersonalize}
      personalizing={personalizing}
      personalizeError={personalizeError}
      personalizeProgress={personalizeProgress}
      aiPersonalization={aiPersonalization}
    />
  );

  return (
    <div className="step-in">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="label-mono inline-flex items-center gap-2">
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
              <Check width={10} height={10} />
            </span>
            {resumed ? "Your saved plan" : "Analysis complete"}
          </span>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">
            {resumed
              ? `${businessName || "Your"}${businessName ? "'s" : ""} AEO plan`
              : `Here's your plan${businessName ? `, ${businessName}` : ""}`}
          </h2>
          <p className="mt-1 max-w-2xl text-ink-500">
            {resumed
              ? "Pick up where you left off — your progress is saved to this link, on any device."
              : "Work through it at your own pace — start with the quick wins."}
          </p>
        </div>
        <button onClick={onEdit} className="btn-ghost text-[13px]">
          {resumed ? "← Start a new plan" : "← Change my answers"}
        </button>
      </div>

      {/* v5 CH-10: the buyer-journey / progress visual sits at the TOP of the whole results
          view (above the tabs), so the first thing the user sees is where they stand. */}
      {profile && <JourneyStrip profile={profile} className="mb-6" />}

      <div role="tablist" aria-label="Plan sections" className="sticky top-0 z-20 mb-6 flex gap-1 overflow-x-auto border-b border-ink/[0.08] bg-paper/90 pb-px backdrop-blur supports-[backdrop-filter]:bg-paper/75">
        {tabs.map((t) => (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`relative shrink-0 whitespace-nowrap rounded-t-lg px-4 py-2.5 text-sm transition-colors ${
              tab === t.id ? "font-medium text-ink" : "text-ink-300 hover:text-ink-500"
            }`}
          >
            {t.label}
            {tab === t.id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent" />}
          </button>
        ))}
      </div>

      {/* One persistent tabpanel hosts every tab's content, its id/label following the active
          tab so each tab button's aria-controls resolves to the panel that really holds what's
          on screen. The plan/tracker serves the Roadmap and Strategy tabs as facets of ONE
          mounted subtree (hidden, not unmounted): leaving and returning must not re-sync
          milestones (a DB write), re-fire analytics, or reset open phases — and the plan keeps
          auto-building on load even while the Overview tab is open. */}
      <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
        <div hidden={!planVisible} className="animate-fade-in">
          {planPanel}
        </div>
        {/* ALWAYS MOUNTED, merely hidden — like the plan panel above, and deliberately
            OUTSIDE the key={tab} div below: a keyed wrapper remounts its whole subtree on
            every tab switch, which is exactly what this block must never do. The ticket
            state itself (fetches, the verify poll) lives one level up in usePackTickets, so
            "Mark as done" keeps polling to Verified while the user reads another tab, and
            the selected page survives the trip. */}
        {packContext && (
          <div hidden={tab !== "pages"} className="animate-fade-in">
            <PagesPanel
              packs={packContext.packs}
              selectedPack={packContext.selectedPack}
              onSelectPack={packContext.onSelectPack}
              onUnlock={packContext.onUnlock}
              shareUrl={shareUrl}
              state={packTickets}
            />
          </div>
        )}
        <div key={tab} className="animate-fade-in">
          {tab === "overview" && profile && (
            <>
              <ScoreRing profile={profile} className="mb-6" />
              <FixImpact data={recheck} />
              <OpenPlanPointer onOpen={() => setTab("strategy")} />
              <OverviewPanel profile={profile} auditJob={auditJob} />
              {/* Moved here from Your plan (item 3.3). These are direction-setting moves,
                  not steps you tick off — they belong beside the score, where the user is
                  orienting, rather than interrupting the do-this-next list. */}
              {extras.kind === "ready" && (
                <div className="mt-6">
                  <StrategyExtras actions={extras.actions} />
                </div>
              )}
              {extras.kind === "pending" && (
                <div className="mt-6 rounded-xl border border-dashed border-ink/15 p-5">
                  <h3 className="text-base font-semibold">Bigger strategic moves</h3>
                  <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-500">
                    Your audit surfaced some direction-setting moves. We&apos;re checking which
                    of them your plan already covers as a concrete task, so you don&apos;t see
                    the same work twice — this appears as soon as your plan is built.
                  </p>
                </div>
              )}
            </>
          )}
          {tab === "blueprint" && plan && <BlueprintPanel sitemap={plan.blueprint.sitemap} topic={plan.blueprint.topic} />}
          {/* "actions" (Roadmap), "strategy", and "kit" are fully served by the
              always-mounted plan panel above. */}
        </div>
      </div>
    </div>
  );
}

// v5 CH-10: the Overview's pointer to the single consolidated plan section (Roadmap +
// Strategy are now one "Your plan" tab).
function OpenPlanPointer({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="card mb-6 flex flex-wrap items-center justify-between gap-3 p-5">
      <div className="min-w-0">
        <h3 className="text-base font-semibold">Your step-by-step plan</h3>
        <p className="mt-0.5 text-sm text-ink-500">
          The prioritized checklist, an automatic site-check, and files to hand off — all in one place.
        </p>
      </div>
      <button type="button" onClick={onOpen} className="btn-primary shrink-0 !py-2 text-[13px]">
        Open your plan
      </button>
    </div>
  );
}

// v5 CH-10: the buyer-journey / progress strip — hoisted to the TOP of the results view.
// Presentational only (no hooks that write to the DB); safe on the resume/no-domain path.
function JourneyStrip({ profile, className = "" }: { profile: SiteProfile; className?: string }) {
  const stages = profile.journey?.stages ?? [];
  if (stages.length === 0) return null;
  const covered = stages.filter((s) => s.covered).length;
  const pct = Math.round((covered / stages.length) * 100);
  return (
    <div className={`card p-5 ${className}`}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <span className="label-mono">How customers find &amp; choose you</span>
        <span className="text-[13px] text-ink-500">
          {covered} of {stages.length} stages covered · {pct}%
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {stages.map((s) => (
          <span
            key={s.stage}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] capitalize ${
              s.covered
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                : "border-ink/10 bg-paper-200/70 text-ink-300"
            }`}
          >
            {s.covered ? <Check width={12} height={12} /> : <span className="h-1.5 w-1.5 rounded-full bg-ink/20" />}
            {humanizeToken(s.stage)}
            <span className="sr-only">{s.covered ? " — covered" : " — missing"}</span>
          </span>
        ))}
      </div>
      <p className="mt-2 text-xs text-ink-300">
        Green stages are covered by your site today; grey ones are where customers lose track of you.
      </p>
    </div>
  );
}

// v5 CH-10: section-level exists / needs-work / missing per buyer-journey stage, each with
// its priority-ranked fix (from profile.actions, whose priority is ASCENDING = most
// important first). Presentational; renders nothing when there are no stages.
function SectionRubric({ profile }: { profile: SiteProfile }) {
  const stages = profile.journey?.stages ?? [];
  if (stages.length === 0) return null;
  const actions = [...(profile.actions ?? [])].sort((a, b) => a.priority - b.priority);

  function statusFor(s: { covered: boolean; present_count: number }): {
    key: string; label: string; cls: string;
  } {
    if (s.covered) return { key: "exists", label: "In place", cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" };
    if (s.present_count > 0) return { key: "wrong", label: "Needs work", cls: "border-rose-500/30 bg-rose-500/10 text-rose-300" };
    return { key: "missing", label: "Missing", cls: "border-ink/10 bg-paper-200/70 text-ink-300" };
  }
  // A stage's fix = the highest-priority action that references it (by category / related slugs).
  function fixFor(stage: string): string | null {
    const tok = stage.toLowerCase();
    const hit = actions.find(
      (a) =>
        a.category?.toLowerCase().includes(tok) ||
        (a.related_slugs ?? []).some((sl) => sl.toLowerCase().includes(tok)) ||
        a.title.toLowerCase().includes(tok),
    );
    return hit ? hit.title : null;
  }
  return (
    <div className="mt-7">
      <span className="label-mono">Section-by-section</span>
      <ul className="m-0 mt-3 flex list-none flex-col gap-2 p-0">
        {stages.map((s) => {
          const st = statusFor(s);
          const fix = st.key === "exists" ? null : fixFor(s.stage);
          return (
            <li key={s.stage} className="flex flex-wrap items-center gap-3 border-t border-ink/[0.06] pt-2">
              <span className="min-w-[120px] text-[13.5px] font-medium capitalize text-ink">{humanizeToken(s.stage)}</span>
              <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] ${st.cls}`}>{st.label}</span>
              {fix && <span className="flex-1 text-[13px] text-ink-500">{fix}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ── live analysis progress (#7) ─────────────────────────────────────────────────

// Maps orchestrator RUN_STAGES → owner-facing copy + a count summary from the event.
const STAGE_LABEL: Record<string, string> = {
  discover: "Finding your pages",
  profile: "Sizing up your site",
  blueprint: "Mapping your ideal site",
  coverage: "Spotting the gaps",
  crawl: "Reading your pages",
  analyze: "Writing recommendations",
  report: "Putting your plan together",
};

// The canonical stage order (mirrors orchestrator.RUN_STAGES) — drives the overall
// progress bar so the wait reads as motion toward done, not an open-ended spinner.
const STAGE_ORDER = ["discover", "profile", "blueprint", "coverage", "crawl", "analyze", "report"] as const;

function stageSummary(stage: string, counts: Record<string, number | string | null>): string {
  const n = (k: string) => (typeof counts[k] === "number" ? (counts[k] as number) : undefined);
  const s = (k: string) => (typeof counts[k] === "string" ? (counts[k] as string) : undefined);
  switch (stage) {
    case "discover": {
      const d = n("discovered");
      return d != null ? `Found ${d} page${d === 1 ? "" : "s"}` : "";
    }
    case "profile": {
      const industry = s("industry");
      const headline = s("headline");
      return headline || (industry ? `Looks like ${industry}` : "");
    }
    case "coverage": {
      const m = n("nodes");
      return m != null ? `Checked against ${m} ideal pages` : "";
    }
    case "crawl": {
      const s = n("scored");
      const f = n("failed");
      if (s == null) return "";
      return `Reviewed ${s} page${s === 1 ? "" : "s"}${f ? ` · ${f} couldn't be read` : ""}`;
    }
    case "analyze": {
      const a = n("analyzed");
      const imp = n("improved");
      if (a == null) return "";
      return `${a} page${a === 1 ? "" : "s"} analyzed${imp != null ? ` · ${imp} with fixes` : ""}`;
    }
    default:
      return "";
  }
}

export function AnalysisProgress({ job, onCancel }: { job: AuditJob; onCancel?: () => void }) {
  const stages = job.stages ?? [];
  const lastStage = stages.length ? stages[stages.length - 1].stage : null;
  const working = job.status === "queued" || job.status === "running";
  const cancelling = job.cancelled && working;

  // Homepage-first partial (R2-2): the structural profile that lands right after
  // discovery, so the owner sees a finding within seconds instead of an empty spinner.
  const profileStage = stages.find((s) => s.stage === "profile");
  const profileHeadline =
    profileStage && typeof profileStage.counts.headline === "string"
      ? (profileStage.counts.headline as string)
      : null;
  const profileIndustry =
    profileStage && typeof profileStage.counts.industry === "string"
      ? (profileStage.counts.industry as string)
      : null;

  // Overall progress: how far through the canonical stage order we've reached. Reads as
  // motion toward done rather than an open-ended wait.
  const reachedIdx = lastStage ? STAGE_ORDER.indexOf(lastStage as (typeof STAGE_ORDER)[number]) : -1;
  const pct =
    job.status === "succeeded"
      ? 100
      : Math.max(6, Math.round(((reachedIdx + 1) / STAGE_ORDER.length) * 100));

  return (
    <div className="step-in rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-5 text-sm">
      <div className="flex items-center justify-between gap-3 text-amber-200">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
          </span>
          <span className="font-medium">
            {cancelling
              ? "Wrapping up early…"
              : job.status === "queued"
                ? "Getting ready to review your site…"
                : "Reviewing your website…"}
          </span>
        </div>
        {working && onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={!!job.cancelled}
            className="btn-ghost shrink-0 !px-2.5 !py-1 text-[11px] text-amber-200/90"
            title="Stop the review — we'll keep whatever we've found so far"
          >
            {job.cancelled ? "Stopping…" : "Stop review"}
          </button>
        )}
      </div>

      {/* overall progress bar — turns the wait into visible motion */}
      <div
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-amber-500/15"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-busy={working}
        aria-label="Analysis progress"
      >
        <div
          className="h-full rounded-full bg-amber-400/80 transition-[width] duration-700 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      {(profileHeadline || profileIndustry) && (
        <div className="step-in mt-4 rounded-lg border border-amber-500/20 bg-paper-100/60 px-3.5 py-2.5">
          <span className="label-mono text-amber-200/80">First look</span>
          <p className="mt-0.5 text-sm text-ink">
            {profileHeadline ?? `Looks like a ${profileIndustry} business.`}
          </p>
          <p className="mt-0.5 text-xs text-ink-300">
            Early read from your homepage — the full page-by-page review is still running.
          </p>
        </div>
      )}

      <ol className="mt-4 space-y-2">
        {stages.map((s, i) => {
          const summary = stageSummary(s.stage, s.counts);
          return (
            <li key={`${s.stage}-${i}`} className="step-in flex items-start gap-2.5">
              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white">
                <Check width={10} height={10} />
              </span>
              <span className="min-w-0">
                <span className="font-medium text-ink">{STAGE_LABEL[s.stage] ?? humanizeToken(s.stage)}</span>
                {summary && <span className="block text-xs text-ink-300">{summary}</span>}
              </span>
            </li>
          );
        })}
        {working && lastStage !== "report" && (
          <li className="flex items-center gap-2.5 text-ink-300">
            <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-amber-500/40 border-t-amber-400" />
            <span className="text-xs">
              {job.progress && STAGE_LABEL[job.progress]
                ? `${STAGE_LABEL[job.progress]}…`
                : "Working…"}
            </span>
          </li>
        )}
      </ol>
      <p className="mt-4 border-t border-amber-500/15 pt-3 text-xs text-amber-200/80">
        The thorough review usually takes around 10 minutes — findings appear above as they come in. You
        can leave this tab open.
      </p>
    </div>
  );
}

// ── prefill / profile crawl progress (the fast "take a look" wait) ───────────────

// The named sub-steps the /api/profile round actually performs (homepage crawl +
// Wikidata industry/HQ resolve + on-site competitor mining + services extraction). The
// endpoint is a single request with no server stream, so — like BuildProgress — we drive
// an honest, staged client-side indicator that the parent unmounts the instant the real
// profile lands (so it never hangs near the end).
const PREFILL_STEPS = [
  "Crawling your homepage",
  "Resolving your industry",
  "Finding competitors",
  "Reading your services",
] as const;

/** A lightweight, AnalysisProgress-styled indicator for the seconds-long prefill crawl —
 *  a determinate bar plus a checked-off step list, so the wait reads as motion toward a
 *  prefilled "About you" rather than an open-ended spinner. Reuses the deep-audit amber
 *  visual language for consistency.
 *
 *  The climb caps below 100 so it never falsely completes; when the real profile lands the
 *  parent flips `done`, which snaps the bar to 100% with every step checked for a brief beat
 *  before it unmounts — closure instead of a bar that vanishes at 92% (which reads as stuck
 *  on any crawl slow enough to watch). */
export function PrefillProgress({ done = false }: { done?: boolean }) {
  const reduced = useReducedMotion();
  const [stepIdx, setStepIdx] = useState(0);
  const [pct, setPct] = useState(10);

  // A steady climb capped below 100 so it always reads as motion and never falsely
  // completes — `done` (set by the parent on completion) is what fills it to 100%. Gated on
  // reduced-motion (a JS interval is still motion).
  useEffect(() => {
    if (reduced) return;
    const ceiling = 92;
    const climb = setInterval(() => setPct((p) => Math.min(ceiling, p + 3)), 200);
    return () => clearInterval(climb);
  }, [reduced]);

  useEffect(() => {
    if (reduced) return;
    const rot = setInterval(
      () => setStepIdx((i) => Math.min(i + 1, PREFILL_STEPS.length - 1)),
      900,
    );
    return () => clearInterval(rot);
  }, [reduced]);

  const displayPct = done ? 100 : pct;
  // On completion every step is checked; otherwise the spinner sits on the current one.
  const reachedIdx = done ? PREFILL_STEPS.length : stepIdx;

  // Reduced motion: a calm, static labelled bar — still not a naked spinner.
  if (reduced) {
    return (
      <div className="step-in rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-4 text-sm">
        <p className="mb-2 font-medium text-amber-200">
          {done ? "Got it — filling in your details…" : "Taking a look at your site…"}
        </p>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-amber-500/15"
          role="progressbar"
          aria-valuenow={done ? 100 : undefined}
          aria-valuetext={done ? "Done" : "Reviewing your homepage"}
          aria-label="Prefill progress"
        >
          <div
            className="h-full rounded-full bg-amber-400/80 transition-[width] duration-300"
            style={{ width: done ? "100%" : "33%" }}
          />
        </div>
      </div>
    );
  }

  const rounded = Math.round(displayPct);
  return (
    <div className="step-in rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-4 text-sm">
      <div className="flex items-center gap-2.5 text-amber-200">
        {done ? (
          <span className="flex h-2.5 w-2.5 items-center justify-center rounded-full bg-emerald-500 text-white">
            <Check width={8} height={8} />
          </span>
        ) : (
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
          </span>
        )}
        <span className="font-medium">
          {done ? "Got it — filling in your details…" : "Taking a quick look at your site…"}
        </span>
      </div>

      <div
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-amber-500/15"
        role="progressbar"
        aria-valuenow={rounded}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-busy={!done}
        aria-label="Prefill progress"
      >
        <div
          className="h-full rounded-full bg-amber-400/80 transition-[width] duration-500 ease-out"
          style={{ width: `${displayPct}%` }}
        />
      </div>

      <ol className="mt-3.5 space-y-2">
        {PREFILL_STEPS.map((label, i) => {
          const isDone = i < reachedIdx;
          const isCurrent = !done && i === reachedIdx;
          return (
            <li key={label} className="flex items-center gap-2.5">
              {isDone ? (
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-emerald-500 text-white">
                  <Check width={10} height={10} />
                </span>
              ) : isCurrent ? (
                <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-amber-500/40 border-t-amber-400" />
              ) : (
                <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                  <span className="h-1.5 w-1.5 rounded-full bg-ink/20" />
                </span>
              )}
              <span className={isDone || isCurrent ? "text-ink" : "text-ink-300"}>{label}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ── overview ──────────────────────────────────────────────────────────────────

function confidenceWord(value: number): string {
  return value >= 0.75 ? "high confidence" : value >= 0.5 ? "fair confidence" : "best guess";
}

function OverviewPanel({ profile, auditJob }: { profile: SiteProfile; auditJob: AuditJob | null }) {
  const c = profile.classification;
  const b = profile.business_intent;
  const scenario = SCENARIO_LABEL[profile.scenario] ?? humanizeToken(profile.scenario);
  return (
    <div className="card p-6 sm:p-8">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-ink px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-paper-100">
          {scenario}
        </span>
        <span className="text-sm font-medium text-accent">
          {DELIVERABLE_LABEL[profile.deliverable] ?? profile.deliverable}
        </span>
      </div>
      <h3 className="mt-4 text-xl font-semibold">{profile.headline}</h3>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-500">{profile.narrative}</p>

      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric label="Business type" value={humanizeToken(b.model)} sub={confidenceWord(b.confidence)} />
        <Metric
          label="Website today"
          value={humanizeToken(c.site_class)}
          sub={c.page_count === 1 ? "1 page found" : `${c.page_count} pages found`}
        />
        <Metric label="Foundation in place" value={`${Math.round(c.structure_score * 100)}%`} sub="of the pages that matter" />
        <Metric
          label="Gaps to fill"
          value={`${profile.journey.gaps.length}`}
          sub={profile.journey.gaps.map(humanizeToken).join(", ") || "none — nice work"}
        />
      </div>

      {/* The journey chips now live in the hoisted JourneyStrip at the top of the results
          view (CH-10); here we surface the section-by-section exists/needs-work/missing +
          the ranked fix per section. */}
      <SectionRubric profile={profile} />

      {auditJob?.status === "succeeded" && auditJob.result?.run?.run_id != null && (
        <p className="mt-6 border-t border-ink/[0.06] pt-4 text-xs text-ink-300">
          Full site review #{auditJob.result.run.run_id} saved
          {auditJob.result.site_report_id ? ` · report #${auditJob.result.site_report_id}` : ""}.
        </p>
      )}
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border border-ink/[0.06] bg-paper-100 px-3 py-2.5 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-card">
      <div className="label-mono">{label}</div>
      <div className="mt-0.5 font-display text-base font-semibold capitalize">{value}</div>
      <div className="truncate text-xs text-ink-300">{sub}</div>
    </div>
  );
}

// The old standalone Roadmap ("big moves" accordion) and Strategy (difficulty clusters)
// panels are gone: their content merged into the single Strategy tab (the tracked milestone
// list + StrategyExtras via lib/phases.dedupeActionsAgainstPlan), and the Roadmap tab now
// hosts only the gamified quest map.

// ── website plan (ideal sitemap) ────────────────────────────────────────────────

function BlueprintPanel({ sitemap, topic }: { sitemap: SitemapNode[]; topic: string }) {
  const sorted = [...sitemap].sort((a, b) => b.priority - a.priority);
  const maxPriority = Math.max(...sorted.map((n) => n.priority), 1);
  return (
    <div>
      <p className="mb-4 text-sm text-ink-500">
        The <span className="font-mono text-ink">{sitemap.length}</span> pages your site needs to win
        AI answers about <span className="font-medium text-ink">{topic}</span> — most important first.
      </p>
      <div className="overflow-x-auto rounded-xl border border-ink/[0.08]">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="bg-paper-200/70">
            <tr className="label-mono text-left">
              <th className="px-4 py-2.5 font-normal">Page</th>
              <th className="px-4 py-2.5 font-normal">What it does</th>
              <th className="px-4 py-2.5 font-normal">Topic group</th>
              <th className="px-4 py-2.5 font-normal text-right">Importance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink/[0.06]">
            {sorted.map((n) => (
              <tr key={n.slug} className="transition-colors hover:bg-paper-200/40">
                <td className="px-4 py-2.5">
                  <span className="font-medium">{n.title}</span>
                  <span className="ml-2 hidden font-mono text-xs text-ink-300 sm:inline">{n.slug}</span>
                </td>
                <td className="px-4 py-2.5 text-ink-500">
                  <span className="capitalize">{humanizeToken(n.page_type)}</span>
                  <span className="text-ink-300"> · {INTENT_LABEL[n.intent] ?? humanizeToken(n.intent)}</span>
                </td>
                <td className="px-4 py-2.5 capitalize text-ink-500">{n.cluster ? humanizeToken(n.cluster) : "—"}</td>
                <td className="px-4 py-2.5">
                  <div className="ml-auto flex w-24 items-center justify-end gap-2">
                    <span className="h-1 flex-1 overflow-hidden rounded-full bg-ink/[0.07]">
                      <span
                        className="block h-full rounded-full bg-accent transition-[width] duration-500"
                        style={{ width: `${Math.round((n.priority / maxPriority) * 100)}%` }}
                      />
                    </span>
                    <span className="font-mono text-xs text-ink-500">{n.priority.toFixed(2)}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── the interactive, phased plan (#10 / #13) ────────────────────────────────────

function TaskCard({
  task,
  done,
  onToggle,
  onHover,
}: {
  task: PlanTask;
  done: boolean;
  onToggle: () => void;
  onHover?: (id: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  // Critical #3: completing a task is a rewarded micro-moment — a one-shot emerald flash +
  // spring on the row, fired only on check (never on an uncheck, which is a correction).
  const [flash, setFlash] = useState(false);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (flashTimer.current) clearTimeout(flashTimer.current); }, []);

  function handleToggle() {
    if (!done) {
      setFlash(true);
      if (flashTimer.current) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlash(false), 700);
    }
    onToggle();
  }

  return (
    <li
      className={`overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100 ${flash ? "task-done-flash" : ""}`}
      onMouseEnter={() => onHover?.(task.id)}
      onMouseLeave={() => onHover?.(null)}
      onFocus={() => onHover?.(task.id)}
      onBlur={() => onHover?.(null)}
    >
      <div className="flex items-start gap-3 p-3.5">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent transition-transform duration-200 ease-out checked:scale-110"
          checked={done}
          onChange={handleToggle}
          aria-label={`Mark "${task.action_required}" done`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`font-medium ${done ? "text-ink-300 line-through" : "text-ink"}`}>{task.label}</span>
            {task.quick_win && (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-300 ring-1 ring-emerald-500/30">
                Quick win
              </span>
            )}
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${EFFORT_PILL[task.effort] ?? "bg-ink/5 text-ink-500"}`}>
              {EFFORT_LABEL[task.effort] ?? humanizeToken(task.effort)}
            </span>
            {/* time/phase as secondary metadata (R2-3) — priority is the folder now */}
            <span className="label-mono rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">
              {PHASE_LABEL[task.phase] ?? humanizeToken(task.phase)}
            </span>
          </div>
          <p className="mt-1 text-sm text-ink-500">{task.action_required}</p>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="mt-1 text-xs text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
            aria-expanded={open}
          >
            {open ? "Hide how-to" : "Show me how →"}
          </button>
        </div>
      </div>

      {open && (
        <div className="border-t border-ink/[0.06] bg-paper-200/40 px-4 pb-3.5">
          <TaskHowTo
            taskKey={task.id}
            label={task.label}
            currentState={task.current_state}
            actionRequired={task.action_required}
            howTo={task.how_to}
            prompts={task.prompts}
            shareUrl={null}
          />
        </div>
      )}
    </li>
  );
}

// R2-3 progressive disclosure: PRIORITY is the primary axis (resolves the R1-vs-R2
// grouping conflict). Tasks are foldered high/medium/low; time/phase drops to a
// secondary tag on each card. The numeric `priority` is the truest signal where a task
// carries one (page tasks); visibility tasks without one fall back to quick-win, then phase.
type Band = "high" | "medium" | "low";

const BAND_META: Record<Band, { label: string; blurb: string; tag: string }> = {
  high: { label: "High priority", blurb: "Start here — biggest impact.", tag: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30" },
  medium: { label: "Medium priority", blurb: "Strong follow-ups once the big ones land.", tag: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30" },
  low: { label: "Low priority", blurb: "Nice-to-haves for later.", tag: "bg-ink/[0.05] text-ink-500 ring-1 ring-ink/10" },
};

// Time/phase, kept as secondary metadata on each task (no longer a competing folder).
const PHASE_LABEL: Record<string, string> = {
  week_1: "This week",
  week_2_4: "Next few weeks",
  later: "Later",
};

function priorityBand(t: PlanTask): Band {
  if (typeof t.priority === "number") {
    if (t.priority >= 0.6) return "high";
    if (t.priority >= 0.3) return "medium";
    return "low";
  }
  if (t.quick_win || t.phase === "week_1") return "high";
  if (t.phase === "week_2_4") return "medium";
  return "low";
}

// Spec #1 "Today" tray: the 1–3 highest-leverage tasks to do right now (quick-wins first,
// then earliest phase, then priority). A focused "do this next" surface that sits above the
// full priority-band folders (R2-3) without competing — it shrinks as items get checked off.
function TodayTray({
  tasks,
  done,
  onToggle,
  onHover,
}: {
  tasks: PlanTask[];
  done: Set<string>;
  onToggle: (t: PlanTask) => void;
  onHover: (id: string | null) => void;
}) {
  const next = tasks
    .filter((t) => !done.has(t.id))
    .sort(
      (a, b) =>
        Number(b.quick_win) - Number(a.quick_win) ||
        (PHASE_RANK[a.phase] ?? 9) - (PHASE_RANK[b.phase] ?? 9) ||
        (b.priority ?? 0) - (a.priority ?? 0),
    )
    .slice(0, 3);
  if (next.length === 0) return null;

  return (
    <div className="card border-accent/30 bg-accent/[0.04] p-5 sm:p-6">
      <div className="mb-3 flex items-center gap-2">
        <span className="label-mono text-accent">Today</span>
        <span className="text-xs text-ink-300">
          {next.length} thing{next.length === 1 ? "" : "s"} to knock out now
        </span>
      </div>
      <ul className="space-y-2">
        {next.map((t) => (
          <li
            key={t.id}
            className="step-in flex items-start gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-3.5"
            onMouseEnter={() => onHover(t.id)}
            onMouseLeave={() => onHover(null)}
          >
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent transition-transform duration-200 ease-out checked:scale-110"
              checked={done.has(t.id)}
              onChange={() => onToggle(t)}
              onFocus={() => onHover(t.id)}
              onBlur={() => onHover(null)}
              aria-label={`Mark "${t.action_required}" done`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-ink">{t.label}</span>
                {t.quick_win && (
                  <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-300 ring-1 ring-emerald-500/30">
                    Quick win
                  </span>
                )}
              </div>
              <p className="mt-1 text-sm text-ink-500">{t.action_required}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// #5 — the plan-completion ring. Shows where you are now (solid arc) and, while you hover a
// task, a ghosted arc previewing where finishing it gets you — the "what's this task worth?"
// moment. Honest: every task is an equal share of the plan, so the preview is always the
// next +1/total step (no invented per-task weights).
function PlanProgressRing({ pct, previewPct }: { pct: number; previewPct: number | null }) {
  const R = 22;
  const C = 2 * Math.PI * R;
  const showPreview = previewPct !== null && previewPct > pct;
  return (
    <div
      className="relative h-14 w-14 shrink-0"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label="Plan progress"
    >
      <svg viewBox="0 0 56 56" className="h-full w-full -rotate-90" aria-hidden>
        <circle cx="28" cy="28" r={R} fill="none" strokeWidth="5" className="stroke-ink/[0.08]" />
        {showPreview && (
          <circle
            cx="28"
            cy="28"
            r={R}
            fill="none"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={`${(C * (previewPct as number)) / 100} ${C}`}
            className="stroke-accent/30 transition-[stroke-dasharray] duration-300 ease-out"
          />
        )}
        <circle
          cx="28"
          cy="28"
          r={R}
          fill="none"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={`${(C * pct) / 100} ${C}`}
          className="stroke-accent transition-[stroke-dasharray] duration-500 ease-out"
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center font-mono text-xs font-semibold tabular-nums text-ink">
        {showPreview ? (previewPct as number) : pct}%
      </span>
    </div>
  );
}

function PhasedPlanView({
  plan,
  storageKey,
  planStateId,
  initialDone,
  serverBacked = false,
  score = null,
  visible = true,
}: {
  plan: StructuredPlan;
  storageKey: string;
  // Spec #1 (resumable plan): when server-backed, progress is seeded from and mirrored to
  // the persisted plan_state behind a /plan/<id> link, so it survives a device switch.
  planStateId?: string;
  initialDone?: string[];
  serverBacked?: boolean;
  score?: number | null;
  // False while the hosting panel is mounted but hidden (results tabs keep the plan mounted).
  visible?: boolean;
}) {
  const [done, setDone] = useState<Set<string>>(new Set());
  const [hoverId, setHoverId] = useState<string | null>(null);
  const planViewed = useRef(false);

  // Seed progress after mount (never during render — prerender has no storage). A
  // server-backed (resumed) plan seeds from the persisted set; otherwise fall back to
  // this browser's localStorage so progress still survives a refresh.
  useEffect(() => {
    if (serverBacked && initialDone) {
      setDone(new Set(initialDone));
      return;
    }
    try {
      const raw = localStorage.getItem(storageKey);
      setDone(raw ? new Set(JSON.parse(raw) as string[]) : new Set());
    } catch {
      /* private mode / blocked storage — still works, just won't persist */
    }
  }, [storageKey, serverBacked, initialDone]);

  // fire plan_viewed once the plan is actually on screen — the panel can be mounted but
  // hidden behind another results tab, and the metrics' completion rate keys its presented
  // ids on this event, so a hidden fire would skew the denominator
  useEffect(() => {
    if (planViewed.current || !visible) return;
    planViewed.current = true;
    api.track("plan_viewed", { quick_win_ids: plan.quick_win_ids, total: plan.total });
  }, [plan, visible]);

  function toggle(task: PlanTask) {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(task.id)) {
        next.delete(task.id);
      } else {
        next.add(task.id);
        // only the act of completing is a signal (uncheck is a correction)
        api.track("task_marked_done", { task_id: task.id, quick_win: task.quick_win, phase: task.phase });
      }
      try {
        localStorage.setItem(storageKey, JSON.stringify([...next]));
      } catch {
        /* persistence is best-effort */
      }
      // Spec #1: mirror progress to the server so a resumable /plan/<id> link stays in
      // sync across devices (best-effort; the localStorage write above is the fallback).
      if (serverBacked && planStateId) {
        api.updatePlanState(planStateId, { done_task_ids: [...next], score });
      }
      return next;
    });
  }

  const allTasks = plan.phases.flatMap((p) => p.tasks);
  const doneCount = allTasks.filter((t) => done.has(t.id)).length;
  const pct = allTasks.length ? Math.round((doneCount / allTasks.length) * 100) : 0;
  const quickWins = allTasks.filter((t) => t.quick_win);
  const quickWinsDone = quickWins.filter((t) => done.has(t.id)).length;

  // #5 — projected completion if the hovered (not-yet-done) task were checked off.
  const hoverTask = hoverId ? allTasks.find((t) => t.id === hoverId) : null;
  const hoverPreviewPct =
    hoverTask && !done.has(hoverTask.id) && allTasks.length
      ? Math.round(((doneCount + 1) / allTasks.length) * 100)
      : null;

  // Group by priority band, then compute progressive unlock: a band opens once every
  // task in the band above it is complete.
  const banded: Record<Band, PlanTask[]> = { high: [], medium: [], low: [] };
  for (const t of allTasks) banded[priorityBand(t)].push(t);
  const bandOrder = (["high", "medium", "low"] as Band[]).filter((b) => banded[b].length > 0);
  const bandComplete = (b: Band) => banded[b].length > 0 && banded[b].every((t) => done.has(t.id));
  const bandUnlocked = (b: Band): boolean => {
    const i = bandOrder.indexOf(b);
    return i <= 0 || bandOrder.slice(0, i).every(bandComplete);
  };

  return (
    <div className="space-y-6">
      {/* Resume-page headline (Spec #1): the MilestoneDashboard roadmap bar, but driven by
          done-count, NOT crawl verification — the resume/no-domain path has no site to
          re-crawl, so "verified" / "we re-check weekly" would be false here. Gated to
          serverBacked so the local-only build path is unaffected. Updates live as tasks
          are checked (pct/doneCount are reactive). */}
      {serverBacked && (
        <div className="card p-5 sm:p-6">
          <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-base font-semibold">Your implementation roadmap</h3>
            <span className="font-mono text-xs text-ink-500">
              {doneCount} / {plan.total} done
            </span>
          </div>
          <div
            className="mb-4 h-2 overflow-hidden rounded-full bg-ink/[0.07]"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Implementation progress"
          >
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-sm text-ink-500">Your progress is saved to this link, on any device.</p>
        </div>
      )}

      <div className="card p-5 sm:p-6">
        <div className="flex items-center gap-4">
          {/* #5 — the completion ring, with a live hover preview of each task's payoff. */}
          <PlanProgressRing pct={pct} previewPct={hoverPreviewPct} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-base font-semibold">Your step-by-step plan</h3>
              {/* Critical #3: the counter springs up on every check — the running tally. */}
              <span className="font-mono text-xs text-ink-500">
                <Tally value={doneCount} className="text-ink" /> / {allTasks.length} done
              </span>
            </div>
            {hoverPreviewPct !== null ? (
              <p className="step-in mt-1 text-sm text-accent">
                Finish this one: <span className="font-mono font-semibold">{pct}%</span>
                <span aria-hidden> → </span>
                <span className="font-mono font-semibold">{hoverPreviewPct}%</span> complete
              </p>
            ) : pct === 100 ? (
              <p className="mt-1 text-sm font-medium text-emerald-300">
                That&apos;s everything — your business is set up to be the one AI recommends. 🎉
              </p>
            ) : quickWins.length > 0 && quickWinsDone === quickWins.length ? (
              <p className="step-in mt-1 flex items-center gap-1.5 text-sm text-emerald-300">
                <Check className="animate-pop" width={13} height={13} />
                All {quickWins.length} quick win{quickWins.length === 1 ? "" : "s"} cleared — your biggest
                early gains are banked. 🎉
              </p>
            ) : quickWins.length > 0 ? (
              <p className="mt-1 text-sm text-ink-500">
                <span className="font-medium text-emerald-300">Start here:</span> {quickWins.length} quick win
                {quickWins.length === 1 ? "" : "s"} you can knock out fast{" "}
                <span className="text-ink-300">
                  — <Tally value={quickWinsDone} />/{quickWins.length} done.
                </span>
              </p>
            ) : (
              <p className="mt-1 text-sm text-ink-500">Hover any task to see how far it gets you.</p>
            )}
          </div>
        </div>
      </div>

      {pct < 100 && <TodayTray tasks={allTasks} done={done} onToggle={toggle} onHover={setHoverId} />}

      {bandOrder.map((band, idx) => (
        <PriorityGroup
          key={band}
          band={band}
          tasks={banded[band]}
          done={done}
          onToggle={toggle}
          onHover={setHoverId}
          unlocked={bandUnlocked(band)}
          priorLabel={idx > 0 ? BAND_META[bandOrder[idx - 1]].label : undefined}
        />
      ))}
    </div>
  );
}

// One priority folder. The first non-empty band is always open; lower bands stay
// collapsed and reveal progressively as the band above is cleared (Duolingo-style,
// to cut decision fatigue) — but the user can always open one early.
function PriorityGroup({
  band,
  tasks,
  done,
  onToggle,
  onHover,
  unlocked,
  priorLabel,
}: {
  band: Band;
  tasks: PlanTask[];
  done: Set<string>;
  onToggle: (t: PlanTask) => void;
  onHover: (id: string | null) => void;
  unlocked: boolean;
  priorLabel?: string;
}) {
  const [manualOpen, setManualOpen] = useState(false);
  const open = unlocked || manualOpen;
  const meta = BAND_META[band];
  const groupDone = tasks.filter((t) => done.has(t.id)).length;
  // Checked-off work folds behind a small disclosure so a returning user sees what is
  // LEFT, not a wall of completed cards. The band's counts and the progressive unlock
  // still read the full set — this folds rows, it never uncounts them. Unchecking a task
  // inside the fold moves it straight back into the open list.
  const openTasks = tasks.filter((t) => !done.has(t.id));
  const doneTasks = tasks.filter((t) => done.has(t.id));
  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${meta.tag}`}>{meta.label}</span>
          <span className="text-xs text-ink-500">{meta.blurb}</span>
        </div>
        <span className="font-mono text-xs text-ink-300">{groupDone}/{tasks.length}</span>
      </div>
      {open ? (
        <ul className="space-y-2">
          {openTasks.map((t) => (
            <TaskCard key={t.id} task={t} done={false} onToggle={() => onToggle(t)} onHover={onHover} />
          ))}
          {doneTasks.length > 0 && (
            <li>
              <details className="group/done">
                <summary className="cursor-pointer list-none rounded-xl border border-dashed border-ink/15 bg-paper-200/40 px-4 py-2.5 text-[13px] text-ink-300 transition-colors hover:border-ink/25 hover:text-accent">
                  <span className="group-open/done:hidden">
                    ✓ Already done ({doneTasks.length}) — show →
                  </span>
                  <span className="hidden group-open/done:inline">Hide what&apos;s done</span>
                </summary>
                <ul className="mt-2 space-y-2">
                  {doneTasks.map((t) => (
                    <TaskCard key={t.id} task={t} done onToggle={() => onToggle(t)} onHover={onHover} />
                  ))}
                </ul>
              </details>
            </li>
          )}
        </ul>
      ) : (
        <button
          type="button"
          onClick={() => setManualOpen(true)}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-ink/15 bg-paper-200/40 px-4 py-3 text-left text-sm transition-colors hover:border-ink/25 hover:bg-paper-200/70"
        >
          <span className="text-ink-500">
            <span className="font-medium text-ink">{tasks.length} {meta.label.toLowerCase()}</span> task
            {tasks.length === 1 ? "" : "s"}
            {priorLabel ? ` — unlocks as you finish ${priorLabel.toLowerCase()}` : ""}
          </span>
          <span aria-hidden className="shrink-0 text-ink-300">Show now →</span>
        </button>
      )}
    </div>
  );
}

// ── launch kit downloads (secondary to the in-app plan) ──────────────────────────

// Keys mirror Asset.kind in aeo.report.packager.
const KIND_LABEL: Record<string, string> = {
  start_here: "read this first",
  visibility: "get found checklist",
  tips: "how-to guide",
  prompt: "AI prompt",
  page_draft: "page draft",
  hire: "for hiring help",
  readme: "read this first",
  sitemap: "for search engines",
  nav: "menu plan",
  content_briefs: "page-by-page brief",
  linking: "how pages connect",
  schema: "technical extras",
  page_spec: "page blueprint",
  strategy: "action plan",
};

// Critical #4: the "Build my plan" wait used to be a naked spinner. /api/deliverables is a
// single (slow, AI-personalized) call with no server stream, so we drive an *honest*
// determinate bar client-side: an asymptotic trickle toward a ceiling it never quite reaches
// (so it always reads as motion, never falsely completes) plus rotating, insight-bearing
// labels. The bar unmounts the instant the real plan lands and replaces this view.
const BUILD_STEPS_SLOW = [
  "Reading your strategy…",
  "Drafting each page — custom-written for you…",
  "Writing your ready-to-paste AI prompts…",
  "Putting your launch kit together…",
];
const BUILD_STEPS_FAST = [
  "Reading your strategy…",
  "Laying out your tasks in order…",
  "Putting your plan together…",
];

function BuildProgress({ slowMode }: { slowMode: boolean }) {
  const reduced = useReducedMotion();
  const steps = slowMode ? BUILD_STEPS_SLOW : BUILD_STEPS_FAST;
  const [pct, setPct] = useState(4);
  const [stepIdx, setStepIdx] = useState(0);

  // A steady, near-linear climb paced to the expected wait and capped below 100 — so the bar
  // keeps visibly moving the whole time (never an early plateau on a long AI build) and never
  // falsely completes; the real plan replacing this view is the only thing that finishes it.
  // Gated on reduced-motion: a JS interval is still motion, so honor the preference (the CSS
  // media query can't reach setInterval).
  useEffect(() => {
    if (reduced) return;
    const ceiling = 94;
    const step = slowMode ? 0.067 : 4.5; // ~9 min to near-ceiling vs ~8s
    const tick = setInterval(() => setPct((p) => Math.min(ceiling, p + step)), 400);
    return () => clearInterval(tick);
  }, [slowMode, reduced]);

  useEffect(() => {
    if (reduced) return;
    const every = slowMode ? 9000 : 2500;
    const rot = setInterval(() => setStepIdx((i) => Math.min(i + 1, steps.length - 1)), every);
    return () => clearInterval(rot);
  }, [slowMode, steps.length, reduced]);

  const slowNote = "Custom-writing every page — usually around 10 minutes. You can leave this tab open.";
  const fastNote = "Putting your plan together — just a few seconds.";

  // Reduced motion: a calm, static "in progress" state — no creeping bar, no number churn, no
  // label rotation. Still not a naked spinner: a labeled, steady, determinate-looking indicator.
  if (reduced) {
    return (
      <div className="mx-auto mt-6 max-w-sm text-left">
        <p className="mb-2 text-xs font-medium text-ink">{steps[0]}</p>
        <div
          className="h-1.5 overflow-hidden rounded-full bg-ink/[0.07]"
          role="progressbar"
          aria-valuetext="Building your plan"
          aria-label="Building your plan"
        >
          <div className="h-full w-1/3 rounded-full bg-gradient-to-r from-accent to-accent-600" />
        </div>
        <p className="mt-2 text-[11px] text-ink-300">{slowMode ? slowNote : fastNote}</p>
      </div>
    );
  }

  // Near the ceiling the climb slows; swap to an honest "final touches" note so a long tail
  // never reads as a stuck bar contradicting the "~10 minutes" promise.
  const note = pct >= 88 ? "Almost there — putting the final touches on your plan." : slowMode ? slowNote : fastNote;
  const rounded = Math.round(pct);
  return (
    <div className="mx-auto mt-6 max-w-sm text-left">
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="step-in font-medium text-ink" key={stepIdx}>
          {steps[stepIdx]}
        </span>
        <span className="font-mono text-ink-300 tabular-nums">{rounded}%</span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-ink/[0.07]"
        role="progressbar"
        aria-valuenow={rounded}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Building your plan"
      >
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="mt-2 text-[11px] text-ink-300">{note}</p>
    </div>
  );
}

function PlanPanel({
  visible,
  facet,
  deliverables,
  loading,
  slowMode,
  domain,
  businessName,
  cmsType,
  onShareUrl,
  error,
  storageKey,
  packWork,
  resume,
  onGenerate,
  onDownloadZip,
  onPersonalize,
  personalizing,
  personalizeError,
  personalizeProgress,
  aiPersonalization,
}: {
  // False while this panel is mounted but hidden behind another results tab — the tracker
  // below defers celebrations and "viewed" analytics until the user can actually see it.
  visible: boolean;
  // Which face of the plan the active tab wants: the gamified map (Roadmap tab) or the
  // merged actionable list (Strategy tab / no-profile fallback).
  facet: TrackerFacet;
  deliverables: DeliverablesResponse | null;
  loading: boolean;
  slowMode: boolean;
  domain?: string;
  businessName: string;
  cmsType?: string | null;
  /** Pack fixes + shared ticket state to fold into this plan (item 3.4); absent on the
   *  no-domain path. */
  packWork?: PackWork;
  /** Lifts the tracker's share link out for the Pages tab's fix rows. */
  onShareUrl?: (url: string | null) => void;
  error: string | null;
  storageKey: string;
  // Saved-plan hydration: seeds the no-domain checklist from the persisted /plan/<id> state.
  resume?: ResumeContext;
  onGenerate: () => void;
  onDownloadZip: () => void;
  onPersonalize: () => void;
  personalizing: boolean;
  personalizeError: string | null;
  personalizeProgress: string | null;
  aiPersonalization: boolean;
}) {
  const [filesOpen, setFilesOpen] = useState(false);

  // "Bigger strategic moves" no longer renders here either (Phase 3 item 3.3) — the
  // Roadmap↔Strategy merge happens ONCE in ResultsView and shows in the Overview tab, so the
  // domain and no-domain paths cannot drift into showing different lists.

  // Auto-build the plan the moment this panel opens — no "Build my plan" click needed. The
  // build is deterministic + instant, so the user lands straight on the (Quest map) tracker.
  // Fires once; a failed build still falls back to the manual "Try again" button below.
  const autoBuilt = useRef(false);
  useEffect(() => {
    if (!deliverables && !loading && !error && !autoBuilt.current) {
      autoBuilt.current = true;
      onGenerate();
    }
  }, [deliverables, loading, error, onGenerate]);

  // #7 — the empty state. The plan auto-builds on open (above), so the states the user meets
  // are: building (fast bar) → ready, or → error+retry.
  if (!deliverables) {
    return (
      <div className="rounded-xl border border-dashed border-ink/15 p-10 text-center">
        <div className="mx-auto mb-4 h-10 w-10 rounded-lg border border-ink/10 bg-paper blueprint-grid" aria-hidden />
        <h3 className="text-base font-semibold">Your step-by-step plan</h3>
        <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
          A phased, do-it-in-order plan — quick wins first, each task with exactly what to do and a
          ready-made prompt to do it. Work it right here, or download the files to hand off.
        </p>
        <button onClick={onGenerate} disabled={loading} className="btn-accent mt-5">
          {loading ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Building your plan…
            </>
          ) : (
            <>
              {error ? "Try again" : "Build my plan"}
              <ArrowRight />
            </>
          )}
        </button>
        {loading ? (
          <BuildProgress slowMode={false} />
        ) : error ? (
          <p className="step-in mx-auto mt-4 max-w-sm rounded-lg border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-300">
            {error}
          </p>
        ) : (
          <p className="mx-auto mt-3 max-w-sm text-xs text-ink-300">
            Ready in seconds — then tick tasks off as you go. You can download the files anytime.
          </p>
        )}
      </div>
    );
  }

  const hasPlan = deliverables.plan && deliverables.plan.total > 0;
  return (
    <div>
      {hasPlan && deliverables.plan ? (
        // With a real site, the plan becomes a persisted, server-tracked roadmap that the
        // weekly crawl auto-verifies — the quest map on the Roadmap tab and the merged
        // actionable list on the Strategy tab, both reading ONE tracker. Without a site
        // (brief-only flow / resumed no-domain plan), fall back to the local checklist plus
        // the same merged strategic moves, so the Strategy tab still tells the whole story.
        domain ? (
          <TrackerView
            domain={domain}
            plan={deliverables.plan}
            businessName={businessName}
            cmsType={cmsType}
            facet={facet}
            onShareUrl={onShareUrl}
            visible={visible}
            packWork={packWork}
          />
        ) : (
          <div className="space-y-6">
            <PhasedPlanView
              plan={deliverables.plan}
              storageKey={storageKey}
              planStateId={resume?.planStateId}
              initialDone={resume?.initialDone}
              serverBacked={!!resume}
              score={resume?.score ?? null}
              visible={visible}
            />
          </div>
        )
      ) : (
        <div className="rounded-xl border border-dashed border-ink/15 p-8 text-center">
          <h3 className="text-base font-semibold">Your plan is ready</h3>
          <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
            We couldn&apos;t lay this out as an interactive checklist this time, but your full kit is
            ready to download below — or rebuild to try again.
          </p>
          <button onClick={onGenerate} disabled={loading} className="btn-ghost mt-4 text-[13px]">
            ↻ Rebuild my plan
          </button>
        </div>
      )}

      {/* Hidden entirely when this session holds no downloadable assets (a resumed plan
          hydrates the interactive plan only — rebuilding regenerates the files). */}
      {deliverables.assets.length > 0 && (
      <div className="mt-6 rounded-xl border border-ink/[0.08]">
        <button
          type="button"
          onClick={() => setFilesOpen((o) => !o)}
          aria-expanded={filesOpen}
          className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm"
        >
          <span className="font-medium">
            Prefer files? <span className="font-normal text-ink-500">{deliverables.manifest.asset_count} ready to download or hand off</span>
          </span>
          <span aria-hidden className="text-ink-300">{filesOpen ? "−" : "+"}</span>
        </button>
        {filesOpen && (
          <div className="step-in border-t border-ink/[0.06] p-4">
            <PersonalizeFiles
              onPersonalize={onPersonalize}
              personalizing={personalizing}
              error={personalizeError}
              progress={personalizeProgress}
              emphasize={aiPersonalization}
            />
            <div className="mb-3 flex justify-end">
              <button onClick={onDownloadZip} className="btn-primary !py-2 text-[13px]">
                ↓ Download everything (.zip)
              </button>
            </div>
            <ul className="divide-y divide-ink/[0.06] overflow-hidden rounded-xl border border-ink/[0.08]">
              {deliverables.assets.map((a, i) => (
                <li
                  key={a.path}
                  className="step-in flex items-center justify-between gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-paper-200/40"
                  style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}
                >
                  <span className="min-w-0">
                    <span className="font-mono text-[13px]">{a.path}</span>
                    <span className="label-mono ml-2 rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">
                      {KIND_LABEL[a.kind] ?? humanizeToken(a.kind)}
                    </span>
                  </span>
                  <button onClick={() => downloadAsset(a)} className="btn-ghost shrink-0 !px-3 !py-1 text-xs">
                    Download
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      )}
    </div>
  );
}

// Friendly labels for the personalization job's progress stages (backend emits draft/report).
const PERSONALIZE_STAGE: Record<string, string> = {
  draft: "Writing each page for your business…",
  report: "Putting your personalized files together…",
};

// #7 — the optional, explicitly-async upgrade: rewrite the downloadable page drafts with AI.
// The interactive plan above is always instant; this only changes the files. Runs as a
// background job, so it shows honest progress and, if it fails, the ready-made files remain.
function PersonalizeFiles({
  onPersonalize,
  personalizing,
  error,
  progress,
  emphasize,
}: {
  onPersonalize: () => void;
  personalizing: boolean;
  error: string | null;
  progress: string | null;
  emphasize: boolean;
}) {
  return (
    <div
      className={`mb-4 rounded-xl border p-4 ${
        emphasize ? "border-accent/30 bg-accent/[0.05]" : "border-ink/[0.08] bg-paper-200/40"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="flex items-center gap-2 text-sm font-medium text-ink">
            <Sparkle className="text-accent" width={14} height={14} />
            Personalize these files with AI
          </span>
          <p className="mt-1 max-w-md text-xs text-ink-300">
            Your plan above is ready now. Want every downloadable page written for your business? We&apos;ll
            draft them with AI — usually a few minutes, and your ready-made files stay available either way.
          </p>
        </div>
        <button
          onClick={onPersonalize}
          disabled={personalizing}
          className="btn-accent shrink-0 !py-2 text-[13px]"
        >
          {personalizing ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Personalizing…
            </>
          ) : (
            "✨ Personalize with AI"
          )}
        </button>
      </div>
      {personalizing && (
        <>
          {progress && PERSONALIZE_STAGE[progress] && (
            <p className="mt-3 text-xs font-medium text-ink-500">{PERSONALIZE_STAGE[progress]}</p>
          )}
          <BuildProgress slowMode />
        </>
      )}
      {error && !personalizing && (
        <p className="step-in mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {error}
        </p>
      )}
    </div>
  );
}

function downloadAsset(asset: BundleAsset) {
  triggerDownload(new Blob([asset.content], { type: "text/plain;charset=utf-8" }), asset.path.replace(/\//g, "_"));
}

export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

// ── resumable plan view (Spec #1) — what /plan/<id> renders ──────────────────────

// The resumed plan is the SAME tabbed results experience as "Build my plan" (Overview /
// Roadmap / Strategy), hydrated from the persisted plan state instead of wizard state — not
// a separate page. A domain-backed plan re-syncs the milestone tracker (so the quest map
// and Strategy list carry live, crawl-verified progress); a brief-only plan seeds the local
// checklist from the saved done_task_ids and mirrors changes back to this link.
export function ResumedPlanView({ state }: { state: PlanStateResponse }) {
  // The Pages tab was missing on every resumed plan, and the cause was not that the data was
  // unavailable — it was that nobody asked for it. `results.tsx` gates the Pages tab on
  // `packContext`, this view passed none, and `PlanStateResponse.run_id` (types.ts) has been
  // carrying the run id the whole time. So "Resume your plan" showed Overview + Your plan and
  // silently dropped the third tab.
  //
  // That matters more than a missing tab: the packs, their locked/unlocked state, and the
  // per-page fixes are the paid half of the product, and a bookmarked /plan/<id> is exactly
  // how a returning customer comes back to them.
  const { user, openAuth } = useAuth();
  const [packs, setPacks] = useState<PackPreview[]>([]);
  const [selectedPack, setSelectedPack] = useState<number | null>(null);
  const [unlockPack, setUnlockPack] = useState<number | null>(null);
  const [unlockOpen, setUnlockOpen] = useState(false);
  // "Redirect me to the packs" — the ask that opens the Pages tab once it exists. Set by
  // the redeemed sign-in and by a completed unlock; ResultsView honours it even when the
  // tab has not been born yet (packs still loading on a fresh return).
  const [tabRequest, setTabRequest] = useState<{ id: "pages"; nonce: number } | null>(null);
  const askForPagesTab = useCallback(() => setTabRequest({ id: "pages", nonce: Date.now() }), []);

  const loadPacks = useCallback(async () => {
    if (state.run_id == null) return;
    try {
      const res = await api.getPacks(state.run_id);
      setPacks(res.packs);
      // Open the first pack by default so the tab has content rather than a picker and a
      // blank right-hand pane.
      setSelectedPack((cur) => cur ?? res.packs[0]?.pack_index ?? null);
    } catch {
      // Best-effort: a resumed plan without packs still renders Overview and Your plan. The
      // Pages tab simply does not appear, which is the honest outcome — better than a tab
      // that opens onto an error.
    }
  }, [state.run_id]);

  useEffect(() => {
    void loadPacks();
  }, [loadPacks]);

  // Same rule as the studio: anonymous → sign in first, and the pack that was clicked is
  // REMEMBERED across that sign-in (lib/pendingUnlock.ts) rather than discarded.
  const handleUnlock = useCallback(
    (packIndex?: number) => {
      if (!user) {
        rememberPendingUnlock({
          domain: state.domain ?? "",
          packIndex: packIndex ?? null,
          runId: state.run_id ?? undefined,
          planStateId: state.id,
        });
        openAuth("unlock-pack");
        return;
      }
      setUnlockPack(packIndex ?? null);
      setUnlockOpen(true);
    },
    [openAuth, state.domain, state.id, state.run_id, user],
  );

  // Resume the unlock the moment sign-in completes — both the in-page email/password flow
  // (this component never unmounts) and the OAuth round-trip that lands back on /plan/<id>.
  // The round-trip now aims HERE even when the click happened on /studio (AuthModal reads
  // the intent's planStateId), because this is the one page that can rebuild the packs
  // from its id alone. Landing the user in front of them is the other half of the redeem.
  useEffect(() => {
    if (!user) return;
    const pending = readPendingUnlock();
    if (!pending || pending.planStateId !== state.id) return;
    clearPendingUnlock();
    if (pending.packIndex != null) setSelectedPack(pending.packIndex);
    setUnlockPack(pending.packIndex);
    setUnlockOpen(true);
    askForPagesTab();
  }, [askForPagesTab, state.id, user]);

  // Hydrate the plan panel from the saved state — no rebuild needed to see your plan.
  // (Downloadable assets aren't persisted; "Rebuild my plan" regenerates them.)
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(() =>
    state.plan && state.plan.total > 0
      ? { manifest: { bundle: "", asset_count: 0, assets: [] }, plan: state.plan, assets: [] }
      : null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The panel's rebuild path (also the auto-build for a saved-but-empty plan): regenerate
  // the deterministic deliverables from the saved identity, same fast path the studio uses.
  async function regenerate() {
    setLoading(true);
    setError(null);
    try {
      setDeliverables(
        await api.deliverables(
          {
            name: state.business_name?.trim() || state.domain || "My business",
            domain: state.domain ?? undefined,
            services: [],
            competitors: [],
            goals: [],
            use_llm: false,
            draft_limit: 10,
          },
          { signal: AbortSignal.timeout(90_000) },
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  // Zip whatever assets a rebuild produced (the files box only shows once assets exist).
  async function downloadZip() {
    if (!deliverables || deliverables.assets.length === 0) return;
    const { default: JSZip } = await import("jszip");
    const zip = new JSZip();
    for (const asset of deliverables.assets) zip.file(asset.path, asset.content);
    triggerDownload(
      await zip.generateAsync({ type: "blob" }),
      `${state.business_name?.trim() || "aeo"}-launch-kit.zip`,
    );
  }

  return (
    <section className="mx-auto max-w-[1240px] py-12 sm:py-16" style={{ padding: "48px clamp(24px, 5vw, 64px)" }}>
      {/* Upgrade path for brief-only plans: crawl verification needs a site. Framed as an
          add-on so it never reads as discarding the saved plan below. */}
      {!state.domain && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-accent/30 bg-accent/[0.06] p-4">
          <p className="text-sm text-ink-500">
            Tie this plan to your website to unlock the automatic weekly site check.
          </p>
          <Link href="/studio" className="btn-accent shrink-0 inline-flex">
            Build a plan for your site →
          </Link>
        </div>
      )}
      <ResultsView
        businessName={state.business_name?.trim() ?? ""}
        domain={state.domain ?? undefined}
        profile={state.profile}
        plan={null}
        auditJob={null}
        deliverables={deliverables}
        delivLoading={loading}
        delivError={error}
        aiPersonalization={false}
        cmsType={null}
        onGenerateDeliverables={regenerate}
        onPersonalize={() => {}}
        personalizing={false}
        personalizeError={null}
        personalizeProgress={null}
        onDownloadZip={downloadZip}
        onEdit={() => {
          window.location.href = "/studio";
        }}
        // The Pages tab is gated on this. Passing it is the whole fix: `run_id` was always
        // on the saved state, nobody ever asked the server for the packs behind it.
        packContext={
          state.run_id != null && packs.length > 0
            ? {
                runId: state.run_id,
                packs,
                selectedPack,
                onSelectPack: setSelectedPack,
                onUnlock: handleUnlock,
              }
            : undefined
        }
        resume={{ planStateId: state.id, initialDone: state.done_task_ids, score: state.score_snapshot }}
        resumed
        tabRequest={tabRequest}
      />
      {unlockOpen && (state.domain ?? "").trim() && (
        <UnlockModal
          domain={(state.domain ?? "").trim()}
          packIndex={unlockPack ?? undefined}
          runId={state.run_id ?? undefined}
          onUnlocked={() => {
            setUnlockOpen(false);
            void loadPacks();
            // They just paid for (or redeemed) this pack — put its pages in front of them.
            askForPagesTab();
          }}
          onClose={() => setUnlockOpen(false)}
        />
      )}
    </section>
  );
}
