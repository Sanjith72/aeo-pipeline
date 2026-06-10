"use client";

// The results experience — one tabbed dashboard after the analysis instead of three
// extra wizard steps. Every label that comes back from the API gets translated into
// owner language here (effort levels, scenario names, journey stages).

import { useState } from "react";
import type {
  AuditJob,
  BriefPlan,
  BundleAsset,
  DeliverablesResponse,
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

const KIND_LABEL: Record<string, string> = {
  xml: "for search engines",
  json: "settings file",
  jsonld: "code snippet",
  markdown: "content outline",
  md: "content outline",
  csv: "spreadsheet",
  txt: "notes",
};

function LaunchKitPanel({
  deliverables,
  loading,
  onGenerate,
  onDownloadZip,
}: {
  deliverables: DeliverablesResponse | null;
  loading: boolean;
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
              Preparing your kit…
            </>
          ) : (
            <>
              Build my launch kit
              <ArrowRight />
            </>
          )}
        </button>
      </div>
    );
  }
  return (
    <div>
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
