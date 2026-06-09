"use client";

import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "@/lib/api";
import type {
  BriefPlan,
  BriefRequest,
  BundleAsset,
  DeliverablesResponse,
  ProfileResponse,
  SiteProfile,
  SitemapNode,
} from "@/lib/types";

type Mode = "plan" | "profile";
type Tab = "strategy" | "blueprint" | "deliverables";

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

const EFFORT_COLOR: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-rose-100 text-rose-800",
};

export default function Page() {
  const [mode, setMode] = useState<Mode>("plan");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [category, setCategory] = useState("");
  const [servicesText, setServicesText] = useState("");
  const [competitorsText, setCompetitorsText] = useState("");
  const [goalsText, setGoalsText] = useState("");
  const [useLlm, setUseLlm] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<BriefPlan | null>(null);
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null);
  const [tab, setTab] = useState<Tab>("strategy");
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(null);
  const [delivLoading, setDelivLoading] = useState(false);

  const profile: SiteProfile | null = plan?.profile ?? profileResult?.profile ?? null;

  function briefFromForm(): BriefRequest {
    return {
      name: name.trim(),
      domain: domain.trim() || undefined,
      category: category.trim() || undefined,
      services: splitList(servicesText),
      competitors: splitList(competitorsText),
      goals: splitList(goalsText),
      use_llm: useLlm,
    };
  }

  async function onGenerate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPlan(null);
    setProfileResult(null);
    setDeliverables(null);
    setLoading(true);
    try {
      if (mode === "profile") {
        if (!domain.trim()) throw new Error("Enter a website domain to analyze.");
        setProfileResult(await api.profile({ domain: domain.trim(), use_llm: useLlm }));
      } else {
        if (!name.trim()) throw new Error("Enter a business name.");
        setPlan(await api.plan(briefFromForm()));
      }
      setTab("strategy");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function onGenerateDeliverables() {
    setDelivLoading(true);
    setError(null);
    try {
      setDeliverables(await api.deliverables({ ...briefFromForm(), draft_limit: 10 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDelivLoading(false);
    }
  }

  function startOver() {
    setPlan(null);
    setProfileResult(null);
    setDeliverables(null);
    setError(null);
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">AEO Studio</h1>
        <p className="mt-1 text-slate-600">
          A guided path from a business to an AI-search-ready website blueprint, strategy, and
          implementation plan.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {!profile ? (
        <BriefForm
          mode={mode}
          setMode={setMode}
          name={name}
          setName={setName}
          domain={domain}
          setDomain={setDomain}
          category={category}
          setCategory={setCategory}
          servicesText={servicesText}
          setServicesText={setServicesText}
          competitorsText={competitorsText}
          setCompetitorsText={setCompetitorsText}
          goalsText={goalsText}
          setGoalsText={setGoalsText}
          useLlm={useLlm}
          setUseLlm={setUseLlm}
          loading={loading}
          onSubmit={onGenerate}
        />
      ) : (
        <section>
          <ScenarioHeader profile={profile} onStartOver={startOver} />
          <Tabs tab={tab} setTab={setTab} showBlueprint={plan !== null} showDeliverables={mode === "plan"} />
          <div className="mt-6">
            {tab === "strategy" && <StrategyPanel profile={profile} />}
            {tab === "blueprint" && plan && <BlueprintPanel sitemap={plan.blueprint.sitemap} />}
            {tab === "deliverables" && mode === "plan" && (
              <DeliverablesPanel
                deliverables={deliverables}
                loading={delivLoading}
                onGenerate={onGenerateDeliverables}
              />
            )}
          </div>
        </section>
      )}
    </main>
  );
}

interface BriefFormProps {
  mode: Mode;
  setMode: (m: Mode) => void;
  name: string;
  setName: (v: string) => void;
  domain: string;
  setDomain: (v: string) => void;
  category: string;
  setCategory: (v: string) => void;
  servicesText: string;
  setServicesText: (v: string) => void;
  competitorsText: string;
  setCompetitorsText: (v: string) => void;
  goalsText: string;
  setGoalsText: (v: string) => void;
  useLlm: boolean;
  setUseLlm: (v: boolean) => void;
  loading: boolean;
  onSubmit: (e: FormEvent) => void;
}

function BriefForm(props: BriefFormProps) {
  const { mode, setMode } = props;
  return (
    <form onSubmit={props.onSubmit} className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-6 inline-flex rounded-lg border border-slate-200 p-1">
        <button
          type="button"
          onClick={() => setMode("plan")}
          className={`rounded-md px-4 py-1.5 text-sm font-medium ${mode === "plan" ? "bg-brand-600 text-white" : "text-slate-600"}`}
        >
          Plan a new site
        </button>
        <button
          type="button"
          onClick={() => setMode("profile")}
          className={`rounded-md px-4 py-1.5 text-sm font-medium ${mode === "profile" ? "bg-brand-600 text-white" : "text-slate-600"}`}
        >
          Analyze an existing site
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {mode === "plan" && (
          <Field label="Business name *">
            <input className="input" value={props.name} onChange={(e) => props.setName(e.target.value)} placeholder="Acme Security" />
          </Field>
        )}
        <Field label={mode === "profile" ? "Website domain *" : "Domain (planned, optional)"}>
          <input className="input" value={props.domain} onChange={(e) => props.setDomain(e.target.value)} placeholder="acme.com" />
        </Field>
        {mode === "plan" && (
          <>
            <Field label="Industry / category">
              <input className="input" value={props.category} onChange={(e) => props.setCategory(e.target.value)} placeholder="cybersecurity, healthcare, …" />
            </Field>
            <Field label="Services (comma or newline separated)">
              <input className="input" value={props.servicesText} onChange={(e) => props.setServicesText(e.target.value)} placeholder="CTEM, ASM" />
            </Field>
            <Field label="Competitors">
              <input className="input" value={props.competitorsText} onChange={(e) => props.setCompetitorsText(e.target.value)} placeholder="rapid7.com, tenable.com" />
            </Field>
            <Field label="Goals">
              <input className="input" value={props.goalsText} onChange={(e) => props.setGoalsText(e.target.value)} placeholder="rank in AI search, generate leads" />
            </Field>
          </>
        )}
      </div>

      <label className="mt-5 flex items-center gap-2 text-sm text-slate-600">
        <input type="checkbox" checked={props.useLlm} onChange={(e) => props.setUseLlm(e.target.checked)} />
        Use the LLM to tailor prose (slower; deterministic scaffold otherwise)
      </label>

      <button
        type="submit"
        disabled={props.loading}
        className="mt-6 rounded-lg bg-brand-600 px-5 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {props.loading ? "Analyzing…" : mode === "profile" ? "Analyze site" : "Generate plan"}
      </button>

      <style>{`.input{width:100%;border:1px solid #cbd5e1;border-radius:0.5rem;padding:0.5rem 0.75rem;font-size:0.875rem}`}</style>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

function ScenarioHeader({ profile, onStartOver }: { profile: SiteProfile; onStartOver: () => void }) {
  const c = profile.classification;
  const b = profile.business_intent;
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{profile.scenario}</Badge>
            <span className="text-sm font-medium text-brand-700">{profile.deliverable}</span>
          </div>
          <h2 className="mt-2 text-xl font-semibold">{profile.headline}</h2>
          <p className="mt-2 max-w-3xl text-sm text-slate-600">{profile.narrative}</p>
        </div>
        <button onClick={onStartOver} className="shrink-0 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
          Start over
        </button>
      </div>
      <div className="mt-4 flex flex-wrap gap-4 text-sm text-slate-600">
        <Stat label="Business model" value={`${b.model} (${b.decided_by})`} />
        <Stat label="Site class" value={`${c.site_class} · ${c.page_count} pages`} />
        <Stat label="Structure" value={`${Math.round(c.structure_score * 100)}%`} />
        <Stat label="Journey gaps" value={profile.journey.gaps.join(", ") || "none"} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="block text-xs uppercase tracking-wide text-slate-400">{label}</span>
      <span className="font-medium text-slate-700">{value}</span>
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-brand-700">{children}</span>;
}

function Tabs({
  tab,
  setTab,
  showBlueprint,
  showDeliverables,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  showBlueprint: boolean;
  showDeliverables: boolean;
}) {
  const tabs: { id: Tab; label: string; show: boolean }[] = [
    { id: "strategy", label: "Strategy & Action Plan", show: true },
    { id: "blueprint", label: "Ideal Sitemap", show: showBlueprint },
    { id: "deliverables", label: "Deliverables", show: showDeliverables },
  ];
  return (
    <div className="mt-6 flex gap-1 border-b border-slate-200">
      {tabs.filter((t) => t.show).map((t) => (
        <button
          key={t.id}
          onClick={() => setTab(t.id)}
          className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${tab === t.id ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-700"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function StrategyPanel({ profile }: { profile: SiteProfile }) {
  return (
    <div className="space-y-3">
      {profile.actions.map((a) => (
        <div key={a.priority} className="flex gap-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-bold text-brand-700">
            {a.priority}
          </div>
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{a.title}</span>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{a.category}</span>
              <span className={`rounded px-2 py-0.5 text-xs ${EFFORT_COLOR[a.effort] ?? "bg-slate-100 text-slate-600"}`}>{a.effort}</span>
            </div>
            <p className="mt-1 text-sm text-slate-600">{a.detail}</p>
            {a.related_slugs.length > 0 && (
              <p className="mt-1 text-xs text-slate-400">{a.related_slugs.join(", ")}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function BlueprintPanel({ sitemap }: { sitemap: SitemapNode[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400">
          <tr>
            <th className="px-4 py-2">Page</th>
            <th className="px-4 py-2">Type / intent</th>
            <th className="px-4 py-2">Cluster</th>
            <th className="px-4 py-2">Priority</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {[...sitemap].sort((a, b) => b.priority - a.priority).map((n) => (
            <tr key={n.slug}>
              <td className="px-4 py-2">
                <span className="font-medium">{n.title}</span>
                <span className="ml-2 text-slate-400">{n.slug}</span>
              </td>
              <td className="px-4 py-2 text-slate-600">{n.page_type} / {n.intent}</td>
              <td className="px-4 py-2 text-slate-600">{n.cluster ?? "—"}</td>
              <td className="px-4 py-2 text-slate-600">{n.priority.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DeliverablesPanel({
  deliverables,
  loading,
  onGenerate,
}: {
  deliverables: DeliverablesResponse | null;
  loading: boolean;
  onGenerate: () => void;
}) {
  function download(asset: BundleAsset) {
    const blob = new Blob([asset.content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = asset.path.replace(/\//g, "_");
    link.click();
    URL.revokeObjectURL(url);
  }

  if (!deliverables) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
        <p className="text-slate-600">Generate a developer-ready bundle: sitemap.xml, navigation, content briefs, per-page specs (with JSON-LD), and an internal-linking + schema plan.</p>
        <button onClick={onGenerate} disabled={loading} className="mt-4 rounded-lg bg-brand-600 px-5 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50">
          {loading ? "Building bundle…" : "Generate deliverables"}
        </button>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-4 py-3 text-sm text-slate-500">
        {deliverables.manifest.asset_count} files in <span className="font-medium text-slate-700">{deliverables.manifest.bundle}</span>
      </div>
      <ul className="divide-y divide-slate-100">
        {deliverables.assets.map((a) => (
          <li key={a.path} className="flex items-center justify-between px-4 py-2.5 text-sm">
            <span>
              <span className="font-medium">{a.path}</span>
              <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{a.kind}</span>
            </span>
            <button onClick={() => download(a)} className="rounded border border-slate-300 px-3 py-1 text-xs text-brand-700 hover:bg-brand-50">
              Download
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
