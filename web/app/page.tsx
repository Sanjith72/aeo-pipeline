"use client";

import { useState } from "react";
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

const GOAL_OPTIONS = [
  "Rank in AI search / answer engines",
  "Generate leads",
  "Sell products (e-commerce)",
  "Local foot traffic",
  "Establish topical authority",
  "Improve a client's site (agency)",
];

const STEPS = [
  "Business Info",
  "Goals",
  "Website Info",
  "Competitors",
  "Challenges",
  "Analysis",
  "Blueprint",
  "Implementation Plan",
  "Deliverables",
] as const;

export default function Page() {
  const [step, setStep] = useState(0);

  // brief
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [servicesText, setServicesText] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [hasSite, setHasSite] = useState(false);
  const [analyzeLive, setAnalyzeLive] = useState(false);
  const [domain, setDomain] = useState("");
  const [competitorsText, setCompetitorsText] = useState("");
  const [challenges, setChallenges] = useState("");
  const [useLlm, setUseLlm] = useState(false);

  // results
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<BriefPlan | null>(null);
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null);
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(null);
  const [delivLoading, setDelivLoading] = useState(false);

  const liveMode = hasSite && analyzeLive && domain.trim().length > 0;
  const profile: SiteProfile | null = plan?.profile ?? profileResult?.profile ?? null;
  const analyzed = profile !== null;

  function briefFromForm(): BriefRequest {
    return {
      name: name.trim(),
      domain: domain.trim() || undefined,
      category: category.trim() || undefined,
      location: location.trim() || undefined,
      services: splitList(servicesText),
      competitors: splitList(competitorsText),
      goals,
      use_llm: useLlm,
    };
  }

  function toggleGoal(goal: string) {
    setGoals((prev) => (prev.includes(goal) ? prev.filter((g) => g !== goal) : [...prev, goal]));
  }

  async function runAnalysis() {
    setError(null);
    setPlan(null);
    setProfileResult(null);
    setDeliverables(null);
    setLoading(true);
    try {
      if (liveMode) {
        setProfileResult(await api.profile({ domain: domain.trim(), use_llm: useLlm }));
      } else {
        if (!name.trim()) throw new Error("Enter a business name (step 1).");
        setPlan(await api.plan(briefFromForm()));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function generateDeliverables() {
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

  async function downloadZip() {
    setError(null);
    try {
      const blob = await api.deliverablesZip({ ...briefFromForm(), draft_limit: 10 });
      triggerDownload(blob, `${name.trim() || "aeo"}-bundle.zip`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const canNext = (() => {
    if (step === 0) return name.trim().length > 0 || liveMode;
    if (step === 5) return analyzed; // must analyze before advancing
    return true;
  })();

  return (
    <main className="mx-auto max-w-6xl px-4 py-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">AEO Studio</h1>
        <p className="mt-1 text-slate-600">
          A guided path from a business to an AI-search-ready website blueprint, strategy, and
          implementation plan.
        </p>
      </header>

      <div className="grid gap-8 md:grid-cols-[220px_1fr]">
        <Stepper current={step} analyzed={analyzed} onJump={setStep} />

        <section>
          {error && (
            <div className="mb-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
              {error}
            </div>
          )}

          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <StepHeader index={step} />

            {step === 0 && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Business name *">
                  <input className="inp" value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Security" />
                </Field>
                <Field label="Industry / category">
                  <input className="inp" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="cybersecurity, healthcare, …" />
                </Field>
                <Field label="Location (optional)">
                  <input className="inp" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Boston, US" />
                </Field>
                <Field label="Services (comma / newline separated)">
                  <input className="inp" value={servicesText} onChange={(e) => setServicesText(e.target.value)} placeholder="CTEM, ASM" />
                </Field>
              </div>
            )}

            {step === 1 && (
              <div className="space-y-2">
                <p className="text-sm text-slate-600">What are you trying to achieve? (select any)</p>
                {GOAL_OPTIONS.map((g) => (
                  <label key={g} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={goals.includes(g)} onChange={() => toggleGoal(g)} />
                    {g}
                  </label>
                ))}
              </div>
            )}

            {step === 2 && (
              <div className="space-y-4">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={hasSite} onChange={(e) => setHasSite(e.target.checked)} />
                  I already have a website
                </label>
                <Field label={hasSite ? "Website domain" : "Planned domain (optional)"}>
                  <input className="inp" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" />
                </Field>
                {hasSite && (
                  <label className="flex items-center gap-2 text-sm text-slate-600">
                    <input type="checkbox" checked={analyzeLive} onChange={(e) => setAnalyzeLive(e.target.checked)} />
                    Crawl &amp; analyze my live site (else we plan the ideal site from the brief)
                  </label>
                )}
              </div>
            )}

            {step === 3 && (
              <Field label="Competitors (comma / newline separated)">
                <textarea className="inp h-28" value={competitorsText} onChange={(e) => setCompetitorsText(e.target.value)} placeholder="rapid7.com, tenable.com" />
              </Field>
            )}

            {step === 4 && (
              <Field label="Current challenges (for context)">
                <textarea className="inp h-28" value={challenges} onChange={(e) => setChallenges(e.target.value)} placeholder="We don't show up in ChatGPT / Perplexity answers…" />
              </Field>
            )}

            {step === 5 && (
              <div>
                <p className="mb-4 text-sm text-slate-600">
                  {liveMode
                    ? `Crawl & classify ${domain.trim()} and route it to a strategy.`
                    : "Generate the ideal-site blueprint and strategy from your brief."}
                </p>
                <label className="mb-4 flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
                  Use the LLM to tailor prose (slower; deterministic scaffold otherwise)
                </label>
                <button onClick={runAnalysis} disabled={loading} className="btn-primary">
                  {loading ? "Analyzing…" : analyzed ? "Re-run analysis" : "Run analysis"}
                </button>
                {analyzed && profile && (
                  <div className="mt-5">
                    <ScenarioHeader profile={profile} />
                  </div>
                )}
              </div>
            )}

            {step === 6 &&
              (plan ? (
                <BlueprintPanel sitemap={plan.blueprint.sitemap} topic={plan.blueprint.topic} />
              ) : (
                <p className="text-sm text-slate-600">
                  Blueprint generation applies to the planned-site flow. For a live-site analysis, see the
                  Implementation Plan next.
                </p>
              ))}

            {step === 7 &&
              (profile ? <StrategyPanel profile={profile} /> : <Empty>Run the analysis first (step 6).</Empty>)}

            {step === 8 && (
              <DeliverablesPanel
                deliverables={deliverables}
                loading={delivLoading}
                onGenerate={generateDeliverables}
                onDownloadZip={downloadZip}
              />
            )}
          </div>

          <div className="mt-6 flex items-center justify-between">
            <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0} className="btn-ghost">
              ← Back
            </button>
            <span className="text-xs text-slate-400">
              Step {step + 1} of {STEPS.length}
            </span>
            <button
              onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
              disabled={step === STEPS.length - 1 || !canNext}
              className="btn-primary"
            >
              {step === 5 && !analyzed ? "Analyze to continue" : "Next →"}
            </button>
          </div>
        </section>
      </div>

      <style>{`
        .inp{width:100%;border:1px solid #cbd5e1;border-radius:0.5rem;padding:0.5rem 0.75rem;font-size:0.875rem}
        .btn-primary{border-radius:0.5rem;background:#1d4ed8;color:#fff;padding:0.5rem 1.25rem;font-weight:500}
        .btn-primary:disabled{opacity:0.5}
        .btn-ghost{border-radius:0.5rem;border:1px solid #cbd5e1;padding:0.5rem 1rem;color:#475569}
        .btn-ghost:disabled{opacity:0.4}
      `}</style>
    </main>
  );
}

function Stepper({ current, analyzed, onJump }: { current: number; analyzed: boolean; onJump: (i: number) => void }) {
  return (
    <nav className="space-y-1">
      {STEPS.map((label, i) => {
        const done = i < current && (i !== 5 || analyzed);
        const active = i === current;
        return (
          <button
            key={label}
            onClick={() => onJump(i)}
            className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${
              active ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs ${
                done ? "bg-green-500 text-white" : active ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-600"
              }`}
            >
              {done ? "✓" : i + 1}
            </span>
            {label}
          </button>
        );
      })}
    </nav>
  );
}

function StepHeader({ index }: { index: number }) {
  const blurbs = [
    "Tell us about your business.",
    "What outcomes matter most?",
    "Do you already have a website?",
    "Who are your competitors?",
    "What's not working today?",
    "Generate your AEO analysis.",
    "Your ideal site architecture.",
    "Do this, in priority order.",
    "Download the developer-ready bundle.",
  ];
  return (
    <div className="mb-5">
      <span className="text-xs font-semibold uppercase tracking-wide text-brand-600">Step {index + 1}</span>
      <h2 className="text-lg font-semibold">{STEPS[index]}</h2>
      <p className="text-sm text-slate-500">{blurbs[index]}</p>
    </div>
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

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate-500">{children}</p>;
}

function ScenarioHeader({ profile }: { profile: SiteProfile }) {
  const c = profile.classification;
  const b = profile.business_intent;
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-brand-700">
          {profile.scenario}
        </span>
        <span className="text-sm font-medium text-brand-700">{profile.deliverable}</span>
      </div>
      <h3 className="mt-2 font-semibold">{profile.headline}</h3>
      <p className="mt-1 text-sm text-slate-600">{profile.narrative}</p>
      <div className="mt-3 flex flex-wrap gap-4 text-sm text-slate-600">
        <span>Model: <b>{b.model}</b> ({b.decided_by})</span>
        <span>Class: <b>{c.site_class}</b> · {c.page_count}p</span>
        <span>Structure: <b>{Math.round(c.structure_score * 100)}%</b></span>
        <span>Gaps: {profile.journey.gaps.join(", ") || "none"}</span>
      </div>
    </div>
  );
}

function StrategyPanel({ profile }: { profile: SiteProfile }) {
  return (
    <div className="space-y-3">
      {profile.actions.map((a) => (
        <div key={a.priority} className="flex gap-4 rounded-lg border border-slate-200 p-4">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-bold text-brand-700">
            {a.priority}
          </div>
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{a.title}</span>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{a.category}</span>
              <span className={`rounded px-2 py-0.5 text-xs ${EFFORT_COLOR[a.effort] ?? "bg-slate-100 text-slate-600"}`}>
                {a.effort}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-600">{a.detail}</p>
            {a.related_slugs.length > 0 && <p className="mt-1 text-xs text-slate-400">{a.related_slugs.join(", ")}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function BlueprintPanel({ sitemap, topic }: { sitemap: SitemapNode[]; topic: string }) {
  return (
    <div>
      <p className="mb-3 text-sm text-slate-500">
        {sitemap.length} ideal pages for <b>{topic}</b>:
      </p>
      <div className="overflow-hidden rounded-lg border border-slate-200">
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
    </div>
  );
}

function DeliverablesPanel({
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
      <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center">
        <p className="text-slate-600">
          Generate a developer-ready bundle: sitemap.xml, navigation, content briefs, per-page specs (with
          JSON-LD), and internal-linking + schema plans.
        </p>
        <button onClick={onGenerate} disabled={loading} className="btn-primary mt-4">
          {loading ? "Building bundle…" : "Generate deliverables"}
        </button>
      </div>
    );
  }
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm text-slate-500">
          {deliverables.manifest.asset_count} files in <b>{deliverables.manifest.bundle}</b>
        </span>
        <button onClick={onDownloadZip} className="btn-primary">
          Download all (.zip)
        </button>
      </div>
      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {deliverables.assets.map((a) => (
          <li key={a.path} className="flex items-center justify-between px-4 py-2.5 text-sm">
            <span>
              <span className="font-medium">{a.path}</span>
              <span className="ml-2 rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{a.kind}</span>
            </span>
            <button onClick={() => downloadAsset(a)} className="rounded border border-slate-300 px-3 py-1 text-xs text-brand-700 hover:bg-brand-50">
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

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
