"use client";

// The results experience — one tabbed dashboard after the analysis instead of extra
// wizard steps. Every label that comes back from the API gets translated into owner
// language here. The centerpiece is the interactive, phased plan (#10/#13): work it in
// the app, check things off, with both an AI prompt and a human how-to per task.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  AuditJob,
  BriefPlan,
  BundleAsset,
  DeliverablesResponse,
  PlanStateResponse,
  PlanTask,
  RecheckStatusResponse,
  SiteProfile,
  SitemapNode,
  StrategyView,
  StructuredPlan,
  VerifiedOutcome,
} from "@/lib/types";
import { DELIVERABLE_LABEL, EFFORT_LABEL, INTENT_LABEL, SCENARIO_LABEL, humanizeToken } from "@/lib/options";
import { aeoScore, aeoScoreCeiling, scoreBand, type ScoreTone } from "@/lib/score";
import { CountUp } from "./motion/primitives";
import { ArrowRight, Check } from "./ui/icons";
import { MilestoneDashboard } from "./MilestoneDashboard";

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
  medium: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
  high: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30",
};

// Phase rank for sorting the "Today" tray (earliest phase first).
const PHASE_RANK: Record<string, number> = { week_1: 0, week_2_4: 1, later: 2 };

type TabId = "overview" | "blueprint" | "actions" | "strategy" | "kit";

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
 *  honest (no self-graded climbing; that's the re-crawl-verified Spec #2). */
export function ScoreRing({ profile, className }: { profile: SiteProfile; className?: string }) {
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
        <span className="label-mono">Your AI visibility score</span>
        <h3 className={`mt-1 text-xl font-semibold ${tone.text}`}>{band.label}</h3>
        <p className="mt-1 max-w-md text-sm text-ink-500">{band.verdict}</p>
        {ceiling > score && (
          <p className="mt-2 text-xs text-ink-300">
            Finish your plan to reach <span className="font-medium text-ink-500">{ceiling}</span> — that's the ghosted
            ring.
          </p>
        )}
      </div>
    </div>
  );
}

// Spec #2 "Verified live": fixes a re-crawl has confirmed actually landed (criterion-honest).
function VerifiedLive({ verified }: { verified: VerifiedOutcome[] }) {
  return (
    <div className="mb-6 rounded-xl border border-emerald-500/30 bg-emerald-500/[0.07] p-5">
      <div className="mb-2 flex items-center gap-2 text-emerald-300">
        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
          <Check width={10} height={10} />
        </span>
        <span className="text-sm font-medium">
          Verified live — {verified.length} fix{verified.length === 1 ? "" : "es"} confirmed by a re-crawl
        </span>
      </div>
      <ul className="space-y-1 text-xs text-ink-400">
        {verified.slice(0, 6).map((v, i) => (
          <li key={`${v.url}-${i}`} className="truncate">
            <span className="text-emerald-300/80">✓</span> {v.criterion ? `${v.criterion} — ` : ""}
            {v.url}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ResultsView({
  businessName,
  domain,
  profile,
  plan,
  auditJob,
  deliverables,
  delivLoading,
  aiPersonalization,
  cmsType,
  onGenerateDeliverables,
  onDownloadZip,
  onEdit,
}: {
  businessName: string;
  domain?: string;
  profile: SiteProfile | null;
  plan: BriefPlan | null;
  auditJob: AuditJob | null;
  deliverables: DeliverablesResponse | null;
  delivLoading: boolean;
  aiPersonalization: boolean;
  // Detected CMS, threaded down to the milestone dashboard's "I'll do it myself" steps.
  cmsType?: string | null;
  onGenerateDeliverables: () => void;
  onDownloadZip: () => void;
  onEdit: () => void;
}) {
  const tabs: { id: TabId; label: string }[] = [
    ...(profile ? [{ id: "overview" as const, label: "Overview" }] : []),
    ...(plan ? [{ id: "blueprint" as const, label: "Your website plan" }] : []),
    ...(profile && profile.actions.length > 0 ? [{ id: "actions" as const, label: "Big moves" }] : []),
    ...(deliverables?.strategy && deliverables.strategy.groups.length > 0
      ? [{ id: "strategy" as const, label: "Strategy" }]
      : []),
    { id: "kit" as const, label: "Your plan" },
  ];
  const [tab, setTab] = useState<TabId>(tabs[0]?.id ?? "kit");

  // Spec #2 "Verified live": a re-crawl can confirm a recommended fix actually landed
  // (criterion-honest). Surface any confirmed-implemented outcomes for this domain in the
  // overview. Best-effort — the API resolves to an empty set on any miss, so this never
  // breaks the results view.
  const [verified, setVerified] = useState<RecheckStatusResponse>({ verified: [], count: 0 });
  useEffect(() => {
    if (profile?.domain) api.recheckStatus(profile.domain).then(setVerified).catch(() => {});
  }, [profile?.domain]);

  // R2-6 nav rework: reserve the tallest panel height we've rendered so switching to a
  // shorter tab never collapses the document and yanks the scroll position. Combined with
  // the sticky tab bar and an opacity-only (no-translate) panel fade, tab switches stay
  // put — no section jump, no scroll-to-top.
  const panelRef = useRef<HTMLDivElement>(null);
  const [minPanelH, setMinPanelH] = useState(0);
  useEffect(() => {
    const h = panelRef.current?.offsetHeight ?? 0;
    if (h > minPanelH) setMinPanelH(h);
  }, [tab, profile, plan, deliverables, minPanelH]);

  return (
    <div className="step-in">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="label-mono inline-flex items-center gap-2">
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
              <Check width={10} height={10} />
            </span>
            Analysis complete
          </span>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">
            Here's your plan{businessName ? `, ${businessName}` : ""}
          </h2>
          <p className="mt-1 max-w-2xl text-ink-500">
            Work through it at your own pace — start with the quick wins.
          </p>
        </div>
        <button onClick={onEdit} className="btn-ghost text-[13px]">
          ← Change my answers
        </button>
      </div>

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

      <div
        key={tab}
        ref={panelRef}
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        className="animate-fade-in"
        style={{ minHeight: minPanelH || undefined }}
      >
        {tab === "overview" && profile && (
          <>
            <ScoreRing profile={profile} className="mb-6" />
            {verified.count > 0 && <VerifiedLive verified={verified.verified} />}
            <OverviewPanel profile={profile} auditJob={auditJob} />
          </>
        )}
        {tab === "blueprint" && plan && <BlueprintPanel sitemap={plan.blueprint.sitemap} topic={plan.blueprint.topic} />}
        {tab === "actions" && profile && <ActionsPanel profile={profile} />}
        {tab === "strategy" && deliverables?.strategy && <StrategyPanel strategy={deliverables.strategy} />}
        {tab === "kit" && (
          <PlanPanel
            deliverables={deliverables}
            loading={delivLoading}
            slowMode={aiPersonalization}
            domain={domain?.trim() || undefined}
            businessName={businessName}
            cmsType={cmsType}
            storageKey={`aeo-plan:${businessName.toLowerCase()}`}
            onGenerate={onGenerateDeliverables}
            onDownloadZip={onDownloadZip}
          />
        )}
      </div>
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
        The thorough review takes a few minutes — findings appear above as they come in. You can leave
        this tab open.
      </p>
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

      {profile.journey.stages.length > 0 && (
        <div className="mt-7">
          <span className="label-mono">How customers find &amp; choose you</span>
          <div className="mt-3 flex flex-wrap gap-2">
            {profile.journey.stages.map((s, i) => (
              <span
                key={s.stage}
                className={`step-in inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] capitalize ${
                  s.covered
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-ink/10 bg-paper-200/70 text-ink-300"
                }`}
                style={{ animationDelay: `${i * 60}ms` }}
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
      )}

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

// ── strategy (high-level profile actions) ───────────────────────────────────────

function ActionsPanel({ profile }: { profile: SiteProfile }) {
  return (
    <div>
      <p className="mb-4 text-sm text-ink-500">
        The big moves, in order — each one says how much work to expect. The step-by-step,
        page-by-page version lives under <span className="font-medium text-ink">Your plan</span>.
      </p>
      <div className="space-y-3">
        {profile.actions.map((a, i) => (
          <div
            key={a.priority}
            className="step-in group flex gap-4 rounded-xl border border-ink/[0.08] bg-paper-100 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-ink/[0.16] hover:shadow-card"
            style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-xs text-paper-100 transition-colors duration-200 group-hover:bg-accent group-hover:text-white">
              {String(a.priority).padStart(2, "0")}
            </div>
            <div className="flex-1">
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
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── strategy (tasks clustered by difficulty / maturity) — R2-5 ───────────────────

const DIFFICULTY_PILL: Record<string, string> = {
  foundation: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
  growth: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
  advanced: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30",
};

function StrategyPanel({ strategy }: { strategy: StrategyView }) {
  return (
    <div>
      <p className="mb-4 text-sm text-ink-500">
        The same work, grouped by how hard it is — start with the foundations, then build up.
        Each group explains what it is, why it matters, and how to approach it.
      </p>
      <div className="space-y-5">
        {strategy.groups.map((g, i) => (
          <div
            key={g.grade}
            className="step-in overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100"
            style={{ animationDelay: `${Math.min(i, 4) * 70}ms` }}
          >
            <div className="border-b border-ink/[0.06] bg-paper-200/40 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${DIFFICULTY_PILL[g.grade] ?? "bg-ink/5 text-ink-500"}`}>
                  {g.difficulty}
                </span>
                <span className="font-semibold">{g.title}</span>
                <span className="font-mono text-xs text-ink-300">{g.tasks.length} task{g.tasks.length === 1 ? "" : "s"}</span>
              </div>
              <div className="mt-2.5 space-y-2">
                <Detail label="What" value={g.readme.what} />
                <Detail label="Why" value={g.readme.why} />
                <Detail label="How" value={g.readme.how} />
              </div>
            </div>
            <ul className="divide-y divide-ink/[0.06]">
              {g.tasks.map((t) => (
                <li key={t.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <span className="min-w-0">
                    <span className="font-medium">{t.label}</span>
                    <span className="mt-0.5 block text-xs text-ink-300">{t.action_required}</span>
                  </span>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${EFFORT_PILL[t.effort] ?? "bg-ink/5 text-ink-500"}`}>
                    {EFFORT_LABEL[t.effort] ?? humanizeToken(t.effort)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

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

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
          () => {},
        );
      }}
      className="btn-ghost !px-2.5 !py-1 text-[11px]"
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}

function TaskCard({
  task,
  done,
  onToggle,
}: {
  task: PlanTask;
  done: boolean;
  onToggle: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <li className="overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100">
      <div className="flex items-start gap-3 p-3.5">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
          checked={done}
          onChange={onToggle}
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
        <div className="step-in space-y-3 border-t border-ink/[0.06] bg-paper-200/40 px-4 py-3.5 text-sm">
          <Detail label="Where you are now" value={task.current_state} />
          <Detail label="What to do" value={task.action_required} />
          <Detail label="How to do it" value={task.how_to} />
          {task.prompts && (
            <div className="grid gap-3 sm:grid-cols-2">
              <PromptBox
                title="Doing it with AI"
                blurb="Paste this into ChatGPT or your builder's AI."
                text={task.prompts.ai}
                copyLabel="Copy prompt"
              />
              <PromptBox
                title="Doing it yourself"
                blurb="A plain checklist if you'd rather write it."
                text={task.prompts.human}
                copyLabel="Copy steps"
              />
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="label-mono">{label}</span>
      <p className="mt-0.5 leading-relaxed text-ink-500">{value}</p>
    </div>
  );
}

function PromptBox({ title, blurb, text, copyLabel }: { title: string; blurb: string; text: string; copyLabel: string }) {
  return (
    <div className="rounded-lg border border-ink/[0.08] bg-paper-100 p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-ink">{title}</span>
        <CopyButton text={text} label={copyLabel} />
      </div>
      <p className="mb-2 text-[11px] text-ink-300">{blurb}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-ink/[0.03] p-2 font-mono text-[11px] leading-relaxed text-ink-500">
        {text}
      </pre>
    </div>
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
}: {
  tasks: PlanTask[];
  done: Set<string>;
  onToggle: (t: PlanTask) => void;
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
          <li key={t.id} className="step-in flex items-start gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-3.5">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
              checked={false}
              onChange={() => onToggle(t)}
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

function PhasedPlanView({
  plan,
  storageKey,
  planStateId,
  initialDone,
  serverBacked = false,
  score = null,
}: {
  plan: StructuredPlan;
  storageKey: string;
  // Spec #1 (resumable plan): when server-backed, progress is seeded from and mirrored to
  // the persisted plan_state behind a /plan/<id> link, so it survives a device switch.
  planStateId?: string;
  initialDone?: string[];
  serverBacked?: boolean;
  score?: number | null;
}) {
  const [done, setDone] = useState<Set<string>>(new Set());
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

  // fire plan_viewed once, with the quick-win ids the metrics' completion rate keys on
  useEffect(() => {
    if (planViewed.current) return;
    planViewed.current = true;
    api.track("plan_viewed", { quick_win_ids: plan.quick_win_ids, total: plan.total });
  }, [plan]);

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
      <div className="card p-5 sm:p-6">
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">Your step-by-step plan</h3>
          <span className="font-mono text-xs text-ink-500">{doneCount} / {allTasks.length} done</span>
        </div>
        <div
          className="mb-4 h-1.5 overflow-hidden rounded-full bg-ink/[0.07]"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Plan progress"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
            style={{ width: `${pct}%` }}
          />
        </div>
        {quickWins.length > 0 && (
          <p className="text-sm text-ink-500">
            <span className="font-medium text-emerald-300">Start here:</span> {quickWins.length} quick win
            {quickWins.length === 1 ? "" : "s"} you can knock out fast
            <span className="text-ink-300"> — {quickWinsDone}/{quickWins.length} done.</span>
          </p>
        )}
        {pct === 100 && (
          <p className="step-in mt-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            That's everything — your business is set up to be the one AI recommends. 🎉
          </p>
        )}
      </div>

      {pct < 100 && <TodayTray tasks={allTasks} done={done} onToggle={toggle} />}

      {bandOrder.map((band, idx) => (
        <PriorityGroup
          key={band}
          band={band}
          tasks={banded[band]}
          done={done}
          onToggle={toggle}
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
  unlocked,
  priorLabel,
}: {
  band: Band;
  tasks: PlanTask[];
  done: Set<string>;
  onToggle: (t: PlanTask) => void;
  unlocked: boolean;
  priorLabel?: string;
}) {
  const [manualOpen, setManualOpen] = useState(false);
  const open = unlocked || manualOpen;
  const meta = BAND_META[band];
  const groupDone = tasks.filter((t) => done.has(t.id)).length;
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
          {tasks.map((t) => (
            <TaskCard key={t.id} task={t} done={done.has(t.id)} onToggle={() => onToggle(t)} />
          ))}
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

function PlanPanel({
  deliverables,
  loading,
  slowMode,
  domain,
  businessName,
  cmsType,
  storageKey,
  onGenerate,
  onDownloadZip,
}: {
  deliverables: DeliverablesResponse | null;
  loading: boolean;
  slowMode: boolean;
  domain?: string;
  businessName: string;
  cmsType?: string | null;
  storageKey: string;
  onGenerate: () => void;
  onDownloadZip: () => void;
}) {
  const [filesOpen, setFilesOpen] = useState(false);

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
              {slowMode ? "Writing your plan with AI — this takes a while…" : "Preparing your plan…"}
            </>
          ) : (
            <>
              Build my plan
              <ArrowRight />
            </>
          )}
        </button>
        <p className="mx-auto mt-3 max-w-sm text-xs text-ink-300">
          {slowMode
            ? "AI personalization is on, so every page is custom-written — expect several minutes. Want it instantly? Turn off “Personalize the wording with AI” under Your goals and rebuild."
            : "Takes a few seconds."}
        </p>
      </div>
    );
  }

  const hasPlan = deliverables.plan && deliverables.plan.total > 0;
  return (
    <div>
      {hasPlan && deliverables.plan ? (
        // With a real site, the plan becomes a persisted, server-tracked roadmap that the
        // weekly crawl auto-verifies. Without one (brief-only flow), fall back to the
        // local, offline checklist so the experience still works with no DB/site.
        domain ? (
          <MilestoneDashboard domain={domain} plan={deliverables.plan} businessName={businessName} cmsType={cmsType} />
        ) : (
          <PhasedPlanView plan={deliverables.plan} storageKey={storageKey} />
        )
      ) : (
        <p className="mb-4 text-sm text-ink-500">Your plan is ready — download the files below.</p>
      )}

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

export function ResumedPlanView({ state }: { state: PlanStateResponse }) {
  const hasPlan = !!state.plan && state.plan.total > 0;
  return (
    <section className="mx-auto max-w-3xl px-5 py-12 sm:py-16">
      <div className="mb-6">
        <span className="label-mono inline-flex items-center gap-2">
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500 text-white">
            <Check width={10} height={10} />
          </span>
          Your saved plan
        </span>
        <h1 className="mt-2 text-2xl font-semibold sm:text-3xl">
          {state.business_name ? `${state.business_name}'s AEO plan` : "Your AEO plan"}
        </h1>
        <p className="mt-1 text-ink-500">
          Pick up where you left off — your progress is saved to this link, on any device.
        </p>
      </div>

      {state.profile && <ScoreRing profile={state.profile} className="mb-8" />}

      {hasPlan ? (
        <PhasedPlanView
          plan={state.plan}
          storageKey={`aeo-plan:resumed:${state.id}`}
          planStateId={state.id}
          initialDone={state.done_task_ids}
          serverBacked
          score={state.score_snapshot}
        />
      ) : (
        <p className="rounded-xl border border-dashed border-ink/15 p-8 text-center text-sm text-ink-500">
          This plan doesn&apos;t have any tasks saved yet.
        </p>
      )}
    </section>
  );
}
