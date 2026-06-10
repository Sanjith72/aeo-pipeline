"use client";

// The results experience — one tabbed dashboard after the analysis instead of three
// extra wizard steps. Every label that comes back from the API gets translated into
// owner language here (effort levels, scenario names, journey stages).

import { useEffect, useState } from "react";
import type {
  AuditJob,
  BriefPlan,
  BundleAsset,
  DeliverablesResponse,
  PlanChecklist,
  SiteProfile,
  SitemapNode,
} from "@/lib/types";
import { DELIVERABLE_LABEL, EFFORT_LABEL, INTENT_LABEL, SCENARIO_LABEL, humanizeToken } from "@/lib/options";
import { ArrowRight, Check } from "./ui/icons";

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-600/20",
  medium: "bg-amber-50 text-amber-700 ring-1 ring-amber-600/20",
  high: "bg-rose-50 text-rose-700 ring-1 ring-rose-600/20",
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
  builderMode,
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
  builderMode: string;
  onGenerateDeliverables: () => void;
  onDownloadZip: () => void;
  onEdit: () => void;
}) {
  const tabs: { id: TabId; label: string }[] = [
    ...(profile ? [{ id: "overview" as const, label: "Overview" }] : []),
    ...(plan ? [{ id: "blueprint" as const, label: "Your website plan" }] : []),
    ...(profile && profile.actions.length > 0 ? [{ id: "actions" as const, label: "Your action plan" }] : []),
    { id: "kit" as const, label: "Launch kit" },
  ];
  const [tab, setTab] = useState<TabId>(tabs[0]?.id ?? "kit");

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
        {tab === "actions" && profile && <ActionsPanel profile={profile} />}
        {tab === "kit" && (
          <LaunchKitPanel
            deliverables={deliverables}
            loading={delivLoading}
            slowMode={aiPersonalization}
            storageKey={`aeo-plan:${businessName.toLowerCase()}:${builderMode}`}
            onGenerate={onGenerateDeliverables}
            onDownloadZip={onDownloadZip}
          />
        )}
      </div>
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
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
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

// ── action plan ───────────────────────────────────────────────────────────────

function ActionsPanel({ profile }: { profile: SiteProfile }) {
  return (
    <div>
      <p className="mb-4 text-sm text-ink-500">
        Do these in order — each one says how much work to expect.
      </p>
      <div className="space-y-3">
        {profile.actions.map((a, i) => (
          <div
            key={a.priority}
            className="step-in group flex gap-4 rounded-xl border border-ink/[0.08] bg-paper-100 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-ink/[0.16] hover:shadow-card"
            style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-xs text-paper-100 transition-colors duration-200 group-hover:bg-accent">
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

// ── website plan ──────────────────────────────────────────────────────────────

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

// ── launch kit ────────────────────────────────────────────────────────────────

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

function PlanChecklistView({ checklist, storageKey }: { checklist: PlanChecklist; storageKey: string }) {
  const [done, setDone] = useState<Set<string>>(new Set());

  // localStorage only after mount — never during render (prerender has no storage)
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      setDone(raw ? new Set(JSON.parse(raw) as string[]) : new Set());
    } catch {
      /* private mode / blocked storage — checklist still works, just won't persist */
    }
  }, [storageKey]);

  function toggle(id: string) {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem(storageKey, JSON.stringify([...next]));
      } catch {
        /* same: persistence is best-effort */
      }
      return next;
    });
  }

  const allIds = checklist.weeks.flatMap((w) => w.tasks.map((t) => t.id));
  const doneCount = allIds.filter((id) => done.has(id)).length;
  const pct = allIds.length ? Math.round((doneCount / allIds.length) * 100) : 0;

  return (
    <div className="card mb-6 p-5 sm:p-6">
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold">Your 30-day plan</h3>
        <span className="font-mono text-xs text-ink-500">
          {doneCount} / {allIds.length} done
        </span>
      </div>
      <div className="mb-5 h-1.5 overflow-hidden rounded-full bg-ink/[0.07]" role="progressbar"
        aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Plan progress">
        <div
          className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        {checklist.weeks.map((week) => (
          <div key={week.title}>
            <div className="mb-2">
              <span className="label-mono">{week.title}</span>
              <span className="ml-2 text-xs text-ink-500">{week.blurb}</span>
            </div>
            <ul className="space-y-1">
              {week.tasks.map((t) => {
                const on = done.has(t.id);
                return (
                  <li key={t.id}>
                    <label className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-ink/[0.03]">
                      <input
                        type="checkbox"
                        className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
                        checked={on}
                        onChange={() => toggle(t.id)}
                      />
                      <span className="min-w-0 text-sm">
                        <span className={on ? "text-ink-300 line-through" : "text-ink"}>{t.label}</span>
                        {t.detail && <span className="block truncate text-xs text-ink-300">{t.detail}</span>}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      {pct === 100 && (
        <p className="step-in mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          That's everything — your business is set up to be the one AI recommends. 🎉
        </p>
      )}
    </div>
  );
}

function LaunchKitPanel({
  deliverables,
  loading,
  slowMode,
  storageKey,
  onGenerate,
  onDownloadZip,
}: {
  deliverables: DeliverablesResponse | null;
  loading: boolean;
  slowMode: boolean;
  storageKey: string;
  onGenerate: () => void;
  onDownloadZip: () => void;
}) {
  if (!deliverables) {
    return (
      <div className="rounded-xl border border-dashed border-ink/15 p-10 text-center">
        <div className="mx-auto mb-4 h-10 w-10 rounded-lg border border-ink/10 bg-paper blueprint-grid" aria-hidden />
        <h3 className="text-base font-semibold">Your launch kit</h3>
        <p className="mx-auto mt-2 max-w-md text-sm text-ink-500">
          A folder of ready-made files — your page list, content outlines for every page, and
          copy-paste snippets. Hand it to whoever builds your website and they can start today.
        </p>
        <button onClick={onGenerate} disabled={loading} className="btn-accent mt-5">
          {loading ? (
            <>
              <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              {slowMode ? "Writing your kit with AI — this takes a while…" : "Preparing your kit…"}
            </>
          ) : (
            <>
              Build my launch kit
              <ArrowRight />
            </>
          )}
        </button>
        <p className="mx-auto mt-3 max-w-sm text-xs text-ink-300">
          {slowMode
            ? "AI personalization is on, so every page outline is custom-written — expect several minutes. Want it instantly? Turn off \"Personalize the wording with AI\" under Your plan and rebuild."
            : "Takes a few seconds."}
        </p>
      </div>
    );
  }
  return (
    <div>
      {deliverables.checklist && deliverables.checklist.total > 0 && (
        <PlanChecklistView checklist={deliverables.checklist} storageKey={storageKey} />
      )}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-ink-500">
          <span className="font-mono text-ink">{deliverables.manifest.asset_count}</span> files, ready to share
        </span>
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
