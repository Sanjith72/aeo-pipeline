"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type {
  AuditJob,
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

const EFFORT_PILL: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-600/20",
  medium: "bg-amber-50 text-amber-700 ring-1 ring-amber-600/20",
  high: "bg-rose-50 text-rose-700 ring-1 ring-rose-600/20",
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
  const [liveDepth, setLiveDepth] = useState<"none" | "quick" | "deep">("none");
  const [domain, setDomain] = useState("");
  const [competitorsText, setCompetitorsText] = useState("");
  const [challenges, setChallenges] = useState("");
  const [useLlm, setUseLlm] = useState(true);

  // results
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<BriefPlan | null>(null);
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null);
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(null);
  const [delivLoading, setDelivLoading] = useState(false);
  const [auditJob, setAuditJob] = useState<AuditJob | null>(null);
  const [deepProfile, setDeepProfile] = useState<SiteProfile | null>(null);

  const quickMode = hasSite && liveDepth === "quick" && domain.trim().length > 0;
  const deepMode = hasSite && liveDepth === "deep" && domain.trim().length > 0;
  const profile: SiteProfile | null = plan?.profile ?? profileResult?.profile ?? deepProfile;
  const analyzed = profile !== null || auditJob?.status === "succeeded";

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
    setAuditJob(null);
    setDeepProfile(null);
    setLoading(true);
    try {
      if (deepMode) {
        await runDeepAudit();
      } else if (quickMode) {
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

  async function runDeepAudit() {
    const { job_id } = await api.startAudit({ domain: domain.trim(), name: name.trim() || domain.trim() });
    let job = await api.auditStatus(job_id);
    setAuditJob(job);
    let tries = 0;
    while ((job.status === "queued" || job.status === "running") && tries < 150) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      job = await api.auditStatus(job_id);
      setAuditJob(job);
      tries += 1;
    }
    if (job.status === "failed") {
      setError(job.error || "audit failed");
      return;
    }
    const runId = job.result?.run?.run_id;
    if (typeof runId === "number") {
      try {
        const rep = await api.siteReport(runId);
        if (rep.sections?.strategy) setDeepProfile(rep.sections.strategy);
      } catch {
        /* the site-report fetch is best-effort — the audit summary still shows */
      }
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
      triggerDownload(
        await api.deliverablesZip({ ...briefFromForm(), draft_limit: 10 }),
        `${name.trim() || "aeo"}-bundle.zip`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const canNext = (() => {
    if (step === 0) return name.trim().length > 0 || quickMode || deepMode;
    if (step === 5) return analyzed;
    return true;
  })();

  return (
    <>
      <TopBar />
      <Hero />

      <section id="studio" className="mx-auto max-w-6xl scroll-mt-20 px-5 pb-24">
        <div className="mb-8 animate-fade-up">
          <span className="label-mono">The workspace</span>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">Build your AEO plan</h2>
          <p className="mt-1 max-w-2xl text-ink-500">
            Nine guided steps. Answer a few questions and walk away with a blueprint, a prioritized
            action plan, and developer-ready assets.
          </p>
        </div>

        <div className="grid animate-fade-up-slow gap-8 md:grid-cols-[230px_1fr]">
          <Stepper current={step} analyzed={analyzed} onJump={setStep} />

          <div>
            {error && (
              <div className="mb-6 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                <span className="label-mono mt-0.5 !text-rose-400">err</span>
                <span>{error}</span>
              </div>
            )}

            <div className="card p-6 sm:p-8">
              <StepHeader index={step} />

              {step === 0 && (
                <div className="grid gap-5 sm:grid-cols-2">
                  <Field label="Business name *">
                    <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Security" />
                  </Field>
                  <Field label="Industry / category">
                    <input className="input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="cybersecurity, healthcare, …" />
                  </Field>
                  <Field label="Location (optional)">
                    <input className="input" value={location} onChange={(e) => setLocation(e.target.value)} placeholder="Boston, US" />
                  </Field>
                  <Field label="Services (comma / newline separated)">
                    <input className="input" value={servicesText} onChange={(e) => setServicesText(e.target.value)} placeholder="CTEM, ASM" />
                  </Field>
                </div>
              )}

              {step === 1 && (
                <div className="grid gap-2.5 sm:grid-cols-2">
                  {GOAL_OPTIONS.map((g) => {
                    const on = goals.includes(g);
                    return (
                      <button
                        key={g}
                        type="button"
                        onClick={() => toggleGoal(g)}
                        className={`flex items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-all ${
                          on ? "border-accent bg-accent-50 text-ink shadow-card" : "border-ink/10 bg-paper-100 text-ink-500 hover:border-ink/25"
                        }`}
                      >
                        <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${on ? "border-accent bg-accent text-white" : "border-ink/30"}`}>
                          {on && <span className="text-[10px] leading-none">✓</span>}
                        </span>
                        {g}
                      </button>
                    );
                  })}
                </div>
              )}

              {step === 2 && (
                <div className="space-y-5">
                  <label className="flex items-center gap-2.5 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-accent" checked={hasSite} onChange={(e) => setHasSite(e.target.checked)} />
                    I already have a website
                  </label>
                  <Field label={hasSite ? "Website domain" : "Planned domain (optional)"}>
                    <input className="input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" />
                  </Field>
                  {hasSite && (
                    <fieldset className="space-y-2">
                      <legend className="field-label">How should we analyze it?</legend>
                      <DepthOption value="none" current={liveDepth} onPick={setLiveDepth} title="Plan the ideal site from the brief" hint="No crawl" />
                      <DepthOption value="quick" current={liveDepth} onPick={setLiveDepth} title="Quick analysis of my live site" hint="Fast · no database" />
                      <DepthOption value="deep" current={liveDepth} onPick={setLiveDepth} title="Deep audit — full crawl + score" hint="Needs backend DB" />
                    </fieldset>
                  )}
                </div>
              )}

              {step === 3 && (
                <Field label="Competitors (comma / newline separated)">
                  <textarea className="input h-28 resize-none" value={competitorsText} onChange={(e) => setCompetitorsText(e.target.value)} placeholder="rapid7.com, tenable.com" />
                </Field>
              )}

              {step === 4 && (
                <Field label="Current challenges (for context)">
                  <textarea className="input h-28 resize-none" value={challenges} onChange={(e) => setChallenges(e.target.value)} placeholder="We don't show up in ChatGPT / Perplexity answers…" />
                </Field>
              )}

              {step === 5 && (
                <div>
                  <p className="mb-5 text-sm text-ink-500">
                    {deepMode
                      ? `Deep audit of ${domain.trim()}: crawl, score, analyze, and build the site report.`
                      : quickMode
                        ? `Quickly classify ${domain.trim()} and route it to a strategy.`
                        : "Generate the ideal-site blueprint and strategy from your brief."}
                  </p>
                  <label className="mb-5 flex items-center gap-2.5 text-sm text-ink-500">
                    <input type="checkbox" className="h-4 w-4 accent-accent" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
                    Use the LLM to tailor prose <span className="text-ink-300">(slower; deterministic scaffold otherwise)</span>
                  </label>
                  <button onClick={runAnalysis} disabled={loading} className="btn-accent">
                    {loading ? (deepMode ? "Auditing…" : "Analyzing…") : analyzed ? "Re-run analysis" : "Run analysis"}
                  </button>
                  {auditJob && <div className="mt-6"><AuditProgress job={auditJob} /></div>}
                  {profile && <div className="mt-6"><ScenarioHeader profile={profile} /></div>}
                </div>
              )}

              {step === 6 &&
                (plan ? (
                  <BlueprintPanel sitemap={plan.blueprint.sitemap} topic={plan.blueprint.topic} />
                ) : (
                  <Empty>Blueprint generation applies to the planned-site flow. For a live-site analysis, see the Implementation Plan next.</Empty>
                ))}

              {step === 7 && (profile ? <StrategyPanel profile={profile} /> : <Empty>Run the analysis first (step 6).</Empty>)}

              {step === 8 && (
                <DeliverablesPanel deliverables={deliverables} loading={delivLoading} onGenerate={generateDeliverables} onDownloadZip={downloadZip} />
              )}
            </div>

            <div className="mt-6 flex items-center justify-between">
              <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0} className="btn-ghost">
                ← Back
              </button>
              <span className="label-mono">
                {String(step + 1).padStart(2, "0")} / {STEPS.length}
              </span>
              <button
                onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
                disabled={step === STEPS.length - 1 || !canNext}
                className="btn-primary"
              >
                {step === 5 && !analyzed ? "Analyze to continue" : "Next →"}
              </button>
            </div>
          </div>
        </div>
      </section>

      <TrustBand />
      <Footer />
    </>
  );
}

// ── chrome ────────────────────────────────────────────────────────────────────

function TopBar() {
  return (
    <header className="sticky top-0 z-40 border-b border-ink/[0.06] bg-paper/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3.5">
        <a href="#" className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-display text-sm font-bold text-paper-100">A</span>
          <span className="font-display text-[15px] font-semibold tracking-tight">AEO Studio</span>
        </a>
        <a href="#studio" className="btn-primary !px-4 !py-2 text-[13px]">
          Start building
        </a>
      </div>
    </header>
  );
}

function Hero() {
  const stats = [
    ["9-step", "guided flow"],
    ["10-criterion", "scoring rubric"],
    ["0", "hallucinated plans"],
  ];
  return (
    <section className="relative overflow-hidden border-b border-ink/[0.06]">
      <div className="blueprint-grid blueprint-grid-fade absolute inset-0" aria-hidden />
      <div className="relative mx-auto max-w-6xl px-5 py-20 sm:py-28">
        <div className="max-w-3xl animate-fade-up">
          <span className="label-mono inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Answer-Engine Optimization
          </span>
          <h1 className="mt-5 text-balance font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            Get found by the engines that <span className="text-accent">answer</span>, not just the ones that search.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-500">
            AEO Studio turns any business — even one without a website — into an AI-search-ready
            blueprint, a prioritized strategy, and a developer-ready implementation plan. Deterministic,
            standards-aligned, and built to ship.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a href="#studio" className="btn-accent !px-6 !py-3 text-[15px]">
              Build my AEO plan →
            </a>
            <a href="#studio" className="btn-ghost !px-6 !py-3 text-[15px]">
              Analyze an existing site
            </a>
          </div>
        </div>

        <dl className="mt-16 grid max-w-2xl grid-cols-3 gap-px overflow-hidden rounded-xl2 border border-ink/[0.08] bg-ink/[0.06] animate-fade-up-slow">
          {stats.map(([big, small]) => (
            <div key={small} className="bg-paper-100 px-5 py-5">
              <dt className="font-display text-2xl font-semibold sm:text-3xl">{big}</dt>
              <dd className="mt-0.5 text-sm text-ink-500">{small}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

function TrustBand() {
  const items = [
    ["Deterministic-first", "The strategy is computed, not guessed. Every recommendation is reproducible — the LLM only enriches prose, it never decides the plan."],
    ["Standards-aligned", "Blueprints map to schema.org structured data, topical-authority clusters, and a 10-criterion AEO rubric the answer engines actually reward."],
    ["Private by design", "Runs on your own stack behind a key-gated API. Your brief and your client data never leave your infrastructure."],
  ];
  return (
    <section className="border-y border-ink/[0.06] bg-paper-200/60">
      <div className="mx-auto max-w-6xl px-5 py-16">
        <span className="label-mono">Why teams trust it</span>
        <div className="mt-6 grid gap-5 md:grid-cols-3">
          {items.map(([title, body]) => (
            <div key={title} className="group card p-6 transition-all duration-200 hover:-translate-y-1 hover:shadow-lift">
              <div className="mb-4 h-9 w-9 rounded-lg border border-ink/10 bg-paper blueprint-grid" aria-hidden />
              <h3 className="text-base font-semibold">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-ink/[0.06]">
      <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-3 px-5 py-8 text-sm text-ink-300 sm:flex-row sm:items-center">
        <span className="font-display font-semibold text-ink-500">AEO Studio</span>
        <span className="label-mono !tracking-[0.1em]">Deterministic AEO · blueprint → strategy → ship</span>
      </div>
    </footer>
  );
}

// ── wizard pieces ───────────────────────────────────────────────────────────

function Stepper({ current, analyzed, onJump }: { current: number; analyzed: boolean; onJump: (i: number) => void }) {
  return (
    <nav className="md:sticky md:top-24 md:self-start">
      <ol className="flex gap-1 overflow-x-auto pb-2 md:flex-col md:gap-0.5 md:overflow-visible md:pb-0">
        {STEPS.map((label, i) => {
          const done = i < current && (i !== 5 || analyzed);
          const active = i === current;
          return (
            <li key={label} className="shrink-0">
              <button
                onClick={() => onJump(i)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                  active ? "bg-ink text-paper-100" : "text-ink-500 hover:bg-ink/[0.04]"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[11px] ${
                    done ? "bg-accent text-white" : active ? "bg-paper-100 text-ink" : "border border-ink/15 text-ink-300"
                  }`}
                >
                  {done ? "✓" : String(i + 1).padStart(2, "0")}
                </span>
                <span className="whitespace-nowrap md:whitespace-normal">{label}</span>
              </button>
            </li>
          );
        })}
      </ol>
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
    <div className="mb-6 border-b border-ink/[0.06] pb-5">
      <span className="label-mono">Step {String(index + 1).padStart(2, "0")}</span>
      <h3 className="mt-1.5 text-xl font-semibold">{STEPS[index]}</h3>
      <p className="mt-1 text-sm text-ink-500">{blurbs[index]}</p>
    </div>
  );
}

function DepthOption({
  value,
  current,
  onPick,
  title,
  hint,
}: {
  value: "none" | "quick" | "deep";
  current: string;
  onPick: (v: "none" | "quick" | "deep") => void;
  title: string;
  hint: string;
}) {
  const on = current === value;
  return (
    <button
      type="button"
      onClick={() => onPick(value)}
      className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left text-sm transition-all ${
        on ? "border-accent bg-accent-50 shadow-card" : "border-ink/10 hover:border-ink/25"
      }`}
    >
      <span className="flex items-center gap-3">
        <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${on ? "border-accent" : "border-ink/30"}`}>
          {on && <span className="h-2 w-2 rounded-full bg-accent" />}
        </span>
        <span className={on ? "text-ink" : "text-ink-500"}>{title}</span>
      </span>
      <span className="label-mono">{hint}</span>
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-ink-300">{children}</p>;
}

function AuditProgress({ job }: { job: AuditJob }) {
  const tone =
    job.status === "succeeded"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : job.status === "failed"
        ? "border-rose-200 bg-rose-50 text-rose-800"
        : "border-amber-200 bg-amber-50 text-amber-800";
  const runId = job.result?.run?.run_id;
  return (
    <div className={`rounded-xl border p-4 text-sm ${tone}`}>
      <div className="flex items-center gap-2">
        {(job.status === "queued" || job.status === "running") && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
        )}
        <span className="label-mono !text-current">{job.status}</span>
        <span>{job.progress}</span>
      </div>
      {job.status === "succeeded" && (
        <p className="mt-1">
          Run #{runId ?? "?"} complete{job.result?.site_report_id ? ` · site report #${job.result.site_report_id}` : ""}.
        </p>
      )}
      {job.error && <p className="mt-1">{job.error}</p>}
    </div>
  );
}

function ScenarioHeader({ profile }: { profile: SiteProfile }) {
  const c = profile.classification;
  const b = profile.business_intent;
  return (
    <div className="rounded-xl border border-ink/[0.08] bg-paper p-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-ink px-3 py-1 font-mono text-[11px] uppercase tracking-[0.14em] text-paper-100">
          {profile.scenario}
        </span>
        <span className="text-sm font-medium text-accent">{profile.deliverable}</span>
      </div>
      <h3 className="mt-3 text-lg font-semibold">{profile.headline}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-500">{profile.narrative}</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Model" value={`${b.model}`} sub={b.decided_by} />
        <Metric label="Site class" value={c.site_class} sub={`${c.page_count} pages`} />
        <Metric label="Structure" value={`${Math.round(c.structure_score * 100)}%`} sub="of archetypes" />
        <Metric label="Journey gaps" value={`${profile.journey.gaps.length}`} sub={profile.journey.gaps.join(", ") || "none"} />
      </div>
    </div>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border border-ink/[0.06] bg-paper-100 px-3 py-2.5">
      <div className="label-mono">{label}</div>
      <div className="mt-0.5 font-display text-base font-semibold capitalize">{value}</div>
      <div className="truncate text-xs text-ink-300">{sub}</div>
    </div>
  );
}

function StrategyPanel({ profile }: { profile: SiteProfile }) {
  return (
    <div className="space-y-3">
      {profile.actions.map((a) => (
        <div key={a.priority} className="group flex gap-4 rounded-xl border border-ink/[0.08] bg-paper-100 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-card">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink font-mono text-xs text-paper-100">
            {String(a.priority).padStart(2, "0")}
          </div>
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{a.title}</span>
              <span className="label-mono rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">{a.category}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${EFFORT_PILL[a.effort] ?? "bg-ink/5 text-ink-500"}`}>{a.effort}</span>
            </div>
            <p className="mt-1 text-sm leading-relaxed text-ink-500">{a.detail}</p>
            {a.related_slugs.length > 0 && <p className="mt-1.5 font-mono text-xs text-ink-300">{a.related_slugs.join("  ·  ")}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function BlueprintPanel({ sitemap, topic }: { sitemap: SitemapNode[]; topic: string }) {
  return (
    <div>
      <p className="mb-4 text-sm text-ink-500">
        <span className="font-mono text-ink">{sitemap.length}</span> ideal pages for{" "}
        <span className="font-medium text-ink">{topic}</span>:
      </p>
      <div className="overflow-hidden rounded-xl border border-ink/[0.08]">
        <table className="w-full text-sm">
          <thead className="bg-paper-200/70">
            <tr className="label-mono text-left">
              <th className="px-4 py-2.5 font-normal">Page</th>
              <th className="px-4 py-2.5 font-normal">Type / intent</th>
              <th className="px-4 py-2.5 font-normal">Cluster</th>
              <th className="px-4 py-2.5 font-normal text-right">Priority</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink/[0.06]">
            {[...sitemap].sort((a, b) => b.priority - a.priority).map((n) => (
              <tr key={n.slug} className="transition-colors hover:bg-paper-200/40">
                <td className="px-4 py-2.5">
                  <span className="font-medium">{n.title}</span>
                  <span className="ml-2 font-mono text-xs text-ink-300">{n.slug}</span>
                </td>
                <td className="px-4 py-2.5 text-ink-500">{n.page_type} / {n.intent}</td>
                <td className="px-4 py-2.5 text-ink-500">{n.cluster ?? "—"}</td>
                <td className="px-4 py-2.5 text-right font-mono text-ink-500">{n.priority.toFixed(2)}</td>
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
      <div className="rounded-xl border border-dashed border-ink/15 p-10 text-center">
        <div className="mx-auto mb-4 h-10 w-10 rounded-lg border border-ink/10 bg-paper blueprint-grid" aria-hidden />
        <p className="mx-auto max-w-md text-sm text-ink-500">
          Generate a developer-ready bundle: <span className="font-mono text-ink">sitemap.xml</span>, navigation,
          content briefs, per-page specs (with JSON-LD), and internal-linking + schema plans.
        </p>
        <button onClick={onGenerate} disabled={loading} className="btn-accent mt-5">
          {loading ? "Building bundle…" : "Generate deliverables"}
        </button>
      </div>
    );
  }
  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-ink-500">
          <span className="font-mono text-ink">{deliverables.manifest.asset_count}</span> files in{" "}
          <span className="font-medium text-ink">{deliverables.manifest.bundle}</span>
        </span>
        <button onClick={onDownloadZip} className="btn-primary !py-2 text-[13px]">
          ↓ Download all (.zip)
        </button>
      </div>
      <ul className="divide-y divide-ink/[0.06] overflow-hidden rounded-xl border border-ink/[0.08]">
        {deliverables.assets.map((a) => (
          <li key={a.path} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-paper-200/40">
            <span className="min-w-0">
              <span className="font-mono text-[13px]">{a.path}</span>
              <span className="label-mono ml-2 rounded bg-ink/[0.04] px-1.5 py-0.5 !tracking-[0.1em]">{a.kind}</span>
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

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
