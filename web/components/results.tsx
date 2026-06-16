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
  PlanPhase,
  PlanStateResponse,
  PlanTask,
  SiteProfile,
  SitemapNode,
  StrategyAction,
  StructuredPlan,
} from "@/lib/types";
import { DELIVERABLE_LABEL, EFFORT_LABEL, INTENT_LABEL, SCENARIO_LABEL, humanizeToken } from "@/lib/options";
import { aeoScore, aeoScoreCeiling, scoreBand, type ScoreTone } from "@/lib/score";
import { CountUp } from "./motion/primitives";
import { ArrowRight, Check } from "./ui/icons";

// Momentum-tier names for the phases — the plan reads as "do now / soon", never a
// calendar countdown that invites "I'll get to the 30-day thing later" (B2). Keyed on the
// backend phase.key so the packager stays untouched.
const PHASE_TITLE: Record<string, string> = {
  week_1: "Do these now",
  week_2_4: "This week",
  later: "Once you're rolling",
};

const PHASE_RANK: Record<string, number> = { week_1: 0, week_2_4: 1, later: 2 };

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30",
  medium: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
  high: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30",
};

type TabId = "overview" | "blueprint" | "actions" | "kit";

export function ResultsView({
  businessName,
  profile,
  plan,
  auditJob,
  deliverables,
  delivLoading,
  aiPersonalization,
  planStateId,
  domain,
  rechecking,
  recheckJob,
  recheckPrevScore,
  onRecheck,
  onGenerateDeliverables,
  onDownloadZip,
  onEdit,
}: {
  businessName: string;
  profile: SiteProfile | null;
  plan: BriefPlan | null;
  auditJob: AuditJob | null;
  deliverables: DeliverablesResponse | null;
  delivLoading: boolean;
  aiPersonalization: boolean;
  planStateId: string | null;
  domain: string;
  rechecking: boolean;
  recheckJob: AuditJob | null;
  recheckPrevScore: number | null;
  onRecheck: () => void;
  onGenerateDeliverables: () => void;
  onDownloadZip: () => void;
  onEdit: () => void;
}) {
  const tabs: { id: TabId; label: string }[] = [
    ...(profile ? [{ id: "overview" as const, label: "Overview" }] : []),
    ...(plan ? [{ id: "blueprint" as const, label: "Your website plan" }] : []),
    ...(profile && profile.actions.length > 0 ? [{ id: "actions" as const, label: "Strategy" }] : []),
    { id: "kit" as const, label: "Your plan" },
  ];
  // Lead with the do-now plan, not the diagnosis dashboard (B2): once there's a plan to
  // work, that's the screen that should greet the user.
  const hasPlan = !!deliverables?.plan && deliverables.plan.total > 0;
  const [tab, setTab] = useState<TabId>(hasPlan ? "kit" : tabs[0]?.id ?? "kit");
  // The canonical score that the ring shows and that progress saves persist.
  const score = profile ? aeoScore(profile) : null;

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

      {profile && <ScoreRing profile={profile} className="mb-8" />}

      <div role="tablist" aria-label="Plan sections" className="mb-6 flex gap-1 overflow-x-auto border-b border-ink/[0.08] pb-px">
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

      <div key={tab} id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} className="step-in">
        {tab === "overview" && profile && <OverviewPanel profile={profile} auditJob={auditJob} />}
        {tab === "blueprint" && plan && <BlueprintPanel sitemap={plan.blueprint.sitemap} topic={plan.blueprint.topic} />}
        {tab === "actions" && profile && (
          <StrategyPanel
            profile={profile}
            domain={domain}
            rechecking={rechecking}
            recheckJob={recheckJob}
            prevScore={recheckPrevScore}
            onRecheck={onRecheck}
          />
        )}
        {tab === "kit" && (
          <PlanPanel
            deliverables={deliverables}
            loading={delivLoading}
            slowMode={aiPersonalization}
            storageKey={`aeo-plan:${businessName.toLowerCase()}`}
            planStateId={planStateId}
            score={score}
            onGenerate={onGenerateDeliverables}
            onDownloadZip={onDownloadZip}
          />
        )}
      </div>
    </div>
  );
}

// ── AEO score ring (B4-lite) ────────────────────────────────────────────────────

// Tone → ring + text colors, keyed by scoreBand().tone. stroke-* utilities color the SVG.
const RING_TONE: Record<ScoreTone, { stroke: string; text: string; soft: string }> = {
  rose: { stroke: "stroke-rose-400", text: "text-rose-300", soft: "text-rose-300/30" },
  amber: { stroke: "stroke-amber-400", text: "text-amber-200", soft: "text-amber-300/30" },
  sky: { stroke: "stroke-sky-400", text: "text-sky-300", soft: "text-sky-300/30" },
  emerald: { stroke: "stroke-emerald-400", text: "text-emerald-300", soft: "text-emerald-300/30" },
};

/** The canonical AEO Score as a gauge: a solid arc for where the site is today and a
 *  ghosted arc for where finishing the plan gets it. The number only really moves on a
 *  re-audit — the plan's progress bar handles task-by-task feedback — so the ring stays
 *  honest (no self-graded climbing; that's a later, re-crawl-verified spec). */
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

// ── live analysis progress (#7) ─────────────────────────────────────────────────

// Maps orchestrator RUN_STAGES → owner-facing copy + a count summary from the event.
const STAGE_LABEL: Record<string, string> = {
  discover: "Finding your pages",
  blueprint: "Mapping your ideal site",
  coverage: "Spotting the gaps",
  crawl: "Reading your pages",
  analyze: "Writing recommendations",
  report: "Putting your plan together",
};

function stageSummary(stage: string, counts: Record<string, number | string | null>): string {
  const n = (k: string) => (typeof counts[k] === "number" ? (counts[k] as number) : undefined);
  switch (stage) {
    case "discover": {
      const d = n("discovered");
      return d != null ? `Found ${d} page${d === 1 ? "" : "s"}` : "";
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

export function AnalysisProgress({ job }: { job: AuditJob }) {
  const stages = job.stages ?? [];
  const lastStage = stages.length ? stages[stages.length - 1].stage : null;
  const working = job.status === "queued" || job.status === "running";
  return (
    <div className="step-in rounded-xl border border-amber-500/30 bg-amber-500/[0.07] p-5 text-sm">
      <div className="flex items-center gap-2.5 text-amber-200">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
        </span>
        <span className="font-medium">
          {job.status === "queued" ? "Getting ready to review your site…" : "Reviewing your website…"}
        </span>
      </div>

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

// ── strategy: priority folders + re-crawl readiness bar ─────────────────────────

// Three importance folders, top of the rank to the bottom. Tones echo the score ring.
const PRIORITY_FOLDERS = [
  { key: "high", title: "High priority", blurb: "Do these first — biggest impact.", badge: "bg-rose-500/10 text-rose-300 ring-1 ring-rose-500/30" },
  { key: "medium", title: "Medium priority", blurb: "Strong follow-ups once the essentials are done.", badge: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30" },
  { key: "low", title: "Low priority", blurb: "Nice-to-haves that round things out.", badge: "bg-sky-500/10 text-sky-300 ring-1 ring-sky-500/30" },
] as const;

// Split the priority-ordered actions into three importance bands.
function bucketActions(actions: StrategyAction[]): StrategyAction[][] {
  const sorted = [...actions].sort((a, b) => a.priority - b.priority);
  const cut = Math.ceil(sorted.length / 3);
  return [sorted.slice(0, cut), sorted.slice(cut, cut * 2), sorted.slice(cut * 2)];
}

function ActionRow({ action }: { action: StrategyAction }) {
  return (
    <div className="flex gap-4 rounded-xl border border-ink/[0.08] bg-paper-100 p-4">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-xs text-paper-100">
        {String(action.priority).padStart(2, "0")}
      </div>
      <div className="flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{action.title}</span>
          <span className="label-mono rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">
            {humanizeToken(action.category)}
          </span>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${EFFORT_PILL[action.effort] ?? "bg-ink/5 text-ink-500"}`}>
            {EFFORT_LABEL[action.effort] ?? humanizeToken(action.effort)}
          </span>
        </div>
        <p className="mt-1 text-sm leading-relaxed text-ink-500">{action.detail}</p>
        {action.related_slugs.length > 0 && (
          <p className="mt-1.5 font-mono text-xs text-ink-300">pages: {action.related_slugs.join("  ·  ")}</p>
        )}
      </div>
    </div>
  );
}

function PriorityFolder({
  title,
  blurb,
  badge,
  actions,
  defaultOpen,
}: {
  title: string;
  blurb: string;
  badge: string;
  actions: StrategyAction[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  return (
    <div className="overflow-hidden rounded-xl border border-ink/[0.08] bg-paper-100">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3.5 text-left transition-colors hover:bg-paper-200/40"
      >
        <span className="flex items-center gap-3">
          <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg font-mono text-sm font-semibold ${badge}`}>
            {actions.length}
          </span>
          <span>
            <span className="block font-medium text-ink">{title}</span>
            <span className="block text-xs text-ink-300">{blurb}</span>
          </span>
        </span>
        <span
          aria-hidden
          className={`shrink-0 text-lg text-ink-300 transition-transform duration-200 ${open ? "rotate-90" : ""}`}
        >
          ›
        </span>
      </button>
      {open && (
        <div className="step-in space-y-2 border-t border-ink/[0.06] bg-paper-200/30 p-3">
          {actions.map((a) => (
            <ActionRow key={a.priority} action={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function StrategyPanel({
  profile,
  domain,
  rechecking,
  recheckJob,
  prevScore,
  onRecheck,
}: {
  profile: SiteProfile;
  domain: string;
  rechecking: boolean;
  recheckJob: AuditJob | null;
  prevScore: number | null;
  onRecheck: () => void;
}) {
  const score = aeoScore(profile);
  const ceiling = aeoScoreCeiling(profile);
  const improved = prevScore != null && score > prevScore;
  const buckets = bucketActions(profile.actions);

  return (
    <div className="space-y-6">
      {/* Re-crawl readiness bar — fills as the rebuilt site is re-crawled. */}
      <div className="card p-5 sm:p-6">
        <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">Your AI readiness</h3>
          <span className="font-mono text-xs text-ink-500">{score} / 100</span>
        </div>
        <div
          className="relative mb-3 h-2 overflow-hidden rounded-full bg-ink/[0.07]"
          role="progressbar"
          aria-valuenow={score}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="AI readiness from your latest site re-check"
        >
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-700 ease-out"
            style={{ width: `${score}%` }}
          />
          {ceiling > score && (
            <span
              aria-hidden
              className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 rounded bg-ink/30"
              style={{ left: `${ceiling}%` }}
              title={`Target: ${ceiling}`}
            />
          )}
        </div>
        <p className="text-sm text-ink-500">
          {domain ? (
            <>
              Build the changes below, publish them, then <span className="font-medium text-ink">re-check your site</span>{" "}
              to watch this climb toward <span className="font-medium text-ink">{ceiling}</span>.
            </>
          ) : (
            <>This is your starting point. Once your site is live, re-check it to watch this climb.</>
          )}
        </p>
        {improved && (
          <p className="step-in mt-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            +{score - (prevScore ?? 0)} since your last check — your changes are landing. 🎉
          </p>
        )}
        {domain && (
          <button onClick={onRecheck} disabled={rechecking} className="btn-accent mt-4 !py-2 text-[13px]">
            {rechecking ? (
              <>
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                Re-checking your site…
              </>
            ) : (
              <>↻ Re-check my site</>
            )}
          </button>
        )}
        {rechecking && recheckJob && (
          <div className="mt-4">
            <AnalysisProgress job={recheckJob} />
          </div>
        )}
      </div>

      {/* Priority folders — open one to see the tasks at that importance. */}
      <div>
        <p className="mb-3 text-sm text-ink-500">
          The big moves, sorted into folders by importance — open a folder to see its tasks. The
          step-by-step, page-by-page version lives under <span className="font-medium text-ink">Your plan</span>.
        </p>
        <div className="space-y-3">
          {PRIORITY_FOLDERS.map((f, i) =>
            buckets[i].length > 0 ? (
              <PriorityFolder
                key={f.key}
                title={f.title}
                blurb={f.blurb}
                badge={f.badge}
                actions={buckets[i]}
                defaultOpen={i === 0}
              />
            ) : null,
          )}
        </div>
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

// Progress persistence (B1). Server is the source of truth once a planStateId exists;
// localStorage is the offline mirror + the fallback when the plan was never persisted
// (API down, or a no-id session). A failed server write never loses the user's check.
function usePlanProgress({
  planStateId,
  storageKey,
  initialDone,
  serverBacked = false,
  score = null,
}: {
  planStateId: string | null;
  storageKey: string;
  initialDone?: string[];
  serverBacked?: boolean;
  score?: number | null;
}) {
  const [done, setDone] = useState<Set<string>>(() => new Set(initialDone ?? []));
  const hydrated = useRef(false);

  // Hydrate once after mount (prerender has no storage). Server-backed views (the
  // /plan/<id> route) trust initialDone even when empty; otherwise read localStorage so an
  // in-session plan still remembers across reloads.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    if (serverBacked) return;
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) setDone(new Set(JSON.parse(raw) as string[]));
    } catch {
      /* private mode / blocked storage — still works, just won't persist */
    }
  }, [storageKey, serverBacked]);

  function persist(next: Set<string>) {
    const ids = [...next];
    try {
      localStorage.setItem(storageKey, JSON.stringify(ids));
    } catch {
      /* best-effort offline mirror */
    }
    if (planStateId) api.updatePlanState(planStateId, { done_task_ids: ids, score });
  }

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
      persist(next);
      return next;
    });
  }

  return { done, toggle };
}

// The do-now surface (B2): the next ≤3 unfinished tasks as one-tap rows, so the user never
// faces the whole month at once. Quick wins first, then earliest phase, then priority.
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
  planStateId = null,
  initialDone,
  serverBacked = false,
  score = null,
}: {
  plan: StructuredPlan;
  storageKey: string;
  planStateId?: string | null;
  initialDone?: string[];
  serverBacked?: boolean;
  score?: number | null;
}) {
  const { done, toggle } = usePlanProgress({ planStateId, storageKey, initialDone, serverBacked, score });
  const [showAll, setShowAll] = useState(false);
  const planViewed = useRef(false);

  // fire plan_viewed once, with the quick-win ids the metrics' completion rate keys on
  useEffect(() => {
    if (planViewed.current) return;
    planViewed.current = true;
    api.track("plan_viewed", { quick_win_ids: plan.quick_win_ids, total: plan.total });
  }, [plan]);

  const allTasks = plan.phases.flatMap((p) => p.tasks);
  const doneCount = allTasks.filter((t) => done.has(t.id)).length;
  const pct = allTasks.length ? Math.round((doneCount / allTasks.length) * 100) : 0;
  const quickWins = allTasks.filter((t) => t.quick_win);
  const quickWinsDone = quickWins.filter((t) => done.has(t.id)).length;

  // The active phase is the first with an unfinished task — what "now" means today. Later
  // phases collapse behind a single "coming up" line so the plan never reads as a 30-day wall.
  const activeIdx = (() => {
    const i = plan.phases.findIndex((p) => p.tasks.some((t) => !done.has(t.id)));
    return i === -1 ? plan.phases.length - 1 : i;
  })();
  const visiblePhases = showAll ? plan.phases : plan.phases.slice(0, activeIdx + 1);
  const hiddenPhases = showAll ? [] : plan.phases.slice(activeIdx + 1);
  const hiddenTaskCount = hiddenPhases.reduce((n, p) => n + p.tasks.length, 0);

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

      {visiblePhases.map((phase, idx) => (
        <PhaseBlock
          key={phase.key}
          phase={phase}
          done={done}
          onToggle={toggle}
          locked={idx > 0 && !plan.phases[idx - 1].tasks.every((t) => done.has(t.id))}
          priorTitle={idx > 0 ? PHASE_TITLE[plan.phases[idx - 1].key] ?? plan.phases[idx - 1].title : undefined}
        />
      ))}

      {hiddenPhases.length > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="flex w-full items-center justify-between gap-3 rounded-xl border border-dashed border-ink/15 px-4 py-3 text-left text-sm text-ink-500 transition-colors hover:border-ink/30 hover:text-ink"
        >
          <span>
            <span className="font-medium text-ink">Coming up</span> — {hiddenTaskCount} more task
            {hiddenTaskCount === 1 ? "" : "s"} once you've cleared this. No rush; you can start anytime.
          </span>
          <span aria-hidden className="shrink-0 text-ink-300">Show everything +</span>
        </button>
      )}
    </div>
  );
}

function PhaseBlock({
  phase,
  done,
  onToggle,
  locked,
  priorTitle,
}: {
  phase: PlanPhase;
  done: Set<string>;
  onToggle: (t: PlanTask) => void;
  locked: boolean;
  priorTitle?: string;
}) {
  const phaseDone = phase.tasks.filter((t) => done.has(t.id)).length;
  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="label-mono">{PHASE_TITLE[phase.key] ?? phase.title}</span>
          <span className="ml-2 text-xs text-ink-500">{phase.blurb}</span>
        </div>
        <span className="font-mono text-xs text-ink-300">{phaseDone}/{phase.tasks.length}</span>
      </div>
      {locked && (
        <p className="mb-2.5 text-xs text-ink-300">
          Tip: finish <span className="text-ink-500">{priorTitle}</span> first — but you can start anytime.
        </p>
      )}
      <ul className={`space-y-2 ${locked ? "opacity-70" : ""}`}>
        {phase.tasks.map((t) => (
          <TaskCard key={t.id} task={t} done={done.has(t.id)} onToggle={() => onToggle(t)} />
        ))}
      </ul>
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
  storageKey,
  planStateId = null,
  score = null,
  onGenerate,
  onDownloadZip,
}: {
  deliverables: DeliverablesResponse | null;
  loading: boolean;
  slowMode: boolean;
  storageKey: string;
  planStateId?: string | null;
  score?: number | null;
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

  return (
    <div>
      {deliverables.plan && deliverables.plan.total > 0 ? (
        <PhasedPlanView plan={deliverables.plan} storageKey={storageKey} planStateId={planStateId} score={score} />
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

// ── resumed plan (B1, the /plan/<id> link) ──────────────────────────────────────

/** The standalone plan behind a resumable link. Self-contained: it renders from the
 *  persisted plan + profile snapshot, so the link works on a fresh device with no wizard
 *  state. Server is the source of truth for progress (serverBacked). */
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
          This plan doesn't have any tasks saved yet.
        </p>
      )}
    </section>
  );
}
