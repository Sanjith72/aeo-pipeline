"use client";

// AEO Studio — five quick questions, then a tabbed results dashboard. Competitors are
// recommended (names only, URLs optional), known answers are dropdowns, and all copy
// is owner language. Marketing chrome lives in components/chrome.tsx, the results
// experience in components/results.tsx.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  AuditJob,
  BriefPlan,
  BriefRequest,
  CompetitorPick,
  DeliverablesResponse,
  ProfileResponse,
  SiteProfile,
} from "@/lib/types";
import { BUSINESS_SIZES, GOAL_OPTIONS, INDUSTRIES, LOCATIONS } from "@/lib/options";
import { Footer, Hero, HowItWorks, TopBar, TrustBand } from "@/components/chrome";
import { ResultsView, triggerDownload } from "@/components/results";
import { CompetitorPicker } from "@/components/CompetitorPicker";
import { Combobox } from "@/components/ui/Combobox";
import { Select } from "@/components/ui/Select";
import { Check } from "@/components/ui/icons";

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

const STEPS = [
  { label: "Your business", blurb: "The basics — so the plan fits you." },
  { label: "Your goals", blurb: "What would success look like?" },
  { label: "Your website", blurb: "Already online? We'll take a look." },
  { label: "Competitors", blurb: "We found some likely ones — just tick the right names." },
  { label: "Your plan", blurb: "One last look, then we build it." },
] as const;

export default function Page() {
  const [step, setStep] = useState(0);
  const [view, setView] = useState<"wizard" | "results">("wizard");

  // the brief
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [sizeBand, setSizeBand] = useState("");
  const [servicesText, setServicesText] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [challenges, setChallenges] = useState("");
  const [hasSite, setHasSite] = useState(false);
  const [liveDepth, setLiveDepth] = useState<"none" | "quick" | "deep">("none");
  const [domain, setDomain] = useState("");
  const [competitors, setCompetitors] = useState<CompetitorPick[]>([]);
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

  const studioRef = useRef<HTMLElement>(null);

  const quickMode = hasSite && liveDepth === "quick" && domain.trim().length > 0;
  const deepMode = hasSite && liveDepth === "deep" && domain.trim().length > 0;
  const profile: SiteProfile | null = plan?.profile ?? profileResult?.profile ?? deepProfile;
  const analyzed = profile !== null || auditJob?.status === "succeeded";

  // moving between steps always shows the top of the new step
  useEffect(() => {
    if (view !== "wizard") return;
    const top = studioRef.current?.getBoundingClientRect().top ?? 0;
    if (top < -40) studioRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [step, view]);

  function briefFromForm(): BriefRequest {
    return {
      name: name.trim(),
      domain: domain.trim() || undefined,
      category: category.trim() || undefined,
      location: location.trim() || undefined,
      services: splitList(servicesText),
      competitors: competitors.map((c) => c.domain?.trim() || c.name),
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
        const ok = await runDeepAudit();
        if (!ok) return;
      } else if (quickMode) {
        setProfileResult(await api.profile({ domain: domain.trim(), use_llm: useLlm }));
      } else {
        setPlan(await api.plan(briefFromForm()));
      }
      setView("results");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function runDeepAudit(): Promise<boolean> {
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
      setError(job.error || "We couldn't finish reviewing your site. Please try again.");
      return false;
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
    return true;
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

  // Zip the assets the browser already holds — never re-generate on the server
  // (each LLM-personalized build can take ~10 minutes on a local model).
  async function downloadZip() {
    if (!deliverables) return;
    setError(null);
    try {
      const { default: JSZip } = await import("jszip");
      const zip = new JSZip();
      for (const asset of deliverables.assets) zip.file(asset.path, asset.content);
      triggerDownload(
        await zip.generateAsync({ type: "blob" }),
        `${name.trim() || "aeo"}-launch-kit.zip`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const nextBlocker: string | null = (() => {
    if (step === 0 && !name.trim()) return "Add your business name to continue";
    if (step === 2 && hasSite && liveDepth !== "none" && !domain.trim())
      return 'Add your website address, or pick "Just plan my ideal website"';
    return null;
  })();

  return (
    <>
      <TopBar />
      <Hero />
      <HowItWorks />

      <section id="studio" ref={studioRef} className="mx-auto max-w-6xl scroll-mt-20 px-5 py-16 sm:py-20">
        <div className="mb-8 animate-fade-up">
          <span className="label-mono">Your plan builder</span>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">
            {view === "results" ? "Your results" : "Five questions. One clear plan."}
          </h2>
          {view === "wizard" && (
            <p className="mt-1 max-w-2xl text-ink-500">
              Answer a few quick questions and get a personalized, step-by-step plan to make your
              business the one AI recommends.
            </p>
          )}
        </div>

        {view === "results" ? (
          <>
            {error && <ErrorNote message={error} />}
            <ResultsView
              businessName={name.trim()}
              profile={profile}
              plan={plan}
              auditJob={auditJob}
              deliverables={deliverables}
              delivLoading={delivLoading}
              aiPersonalization={useLlm}
              onGenerateDeliverables={generateDeliverables}
              onDownloadZip={downloadZip}
              onEdit={() => setView("wizard")}
            />
          </>
        ) : (
          <div className="grid animate-fade-up-slow gap-8 md:grid-cols-[230px_1fr]">
            <Stepper current={step} onJump={setStep} />

            <div>
              {error && <ErrorNote message={error} />}

              <div className="card p-6 sm:p-8">
                <StepHeader index={step} />

                <div key={step} className="step-in">
                  {step === 0 && (
                    <div className="grid gap-5 sm:grid-cols-2">
                      <Field label="Business name" required>
                        <input
                          className="input"
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                          placeholder="e.g. Harbor Dental"
                          autoComplete="organization"
                          aria-label="Business name"
                        />
                      </Field>
                      <Field label="Industry">
                        <Combobox
                          value={category}
                          onChange={setCategory}
                          options={INDUSTRIES}
                          placeholder="Choose or type your own…"
                          ariaLabel="Industry"
                        />
                      </Field>
                      <Field label="Location" hint="optional">
                        <Combobox
                          value={location}
                          onChange={setLocation}
                          options={LOCATIONS}
                          placeholder="City, region, or online only…"
                          ariaLabel="Location"
                        />
                      </Field>
                      <Field label="Team size" hint="optional">
                        <Select
                          value={sizeBand}
                          onChange={setSizeBand}
                          options={BUSINESS_SIZES}
                          placeholder="How big is your team?"
                          ariaLabel="Team size"
                        />
                      </Field>
                      <div className="sm:col-span-2">
                        <Field label="What do you offer?" hint="optional — separate with commas">
                          <input
                            className="input"
                            value={servicesText}
                            onChange={(e) => setServicesText(e.target.value)}
                            placeholder="e.g. teeth whitening, implants, check-ups"
                            aria-label="What do you offer"
                          />
                        </Field>
                      </div>
                    </div>
                  )}

                  {step === 1 && (
                    <div className="space-y-6">
                      <div className="grid gap-2.5 sm:grid-cols-2">
                        {GOAL_OPTIONS.map((g, i) => {
                          const on = goals.includes(g.label);
                          return (
                            <button
                              key={g.label}
                              type="button"
                              aria-pressed={on}
                              onClick={() => toggleGoal(g.label)}
                              className={`option-card !items-start ${on ? "option-card-on" : "option-card-off"} step-in`}
                              style={{ animationDelay: `${i * 50}ms` }}
                            >
                              <span
                                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
                                  on ? "border-accent bg-accent text-white" : "border-ink/25 bg-paper-100"
                                }`}
                              >
                                {on && <Check className="animate-pop" width={12} height={12} />}
                              </span>
                              <span>
                                <span className="block font-medium text-ink">{g.label}</span>
                                <span className="mt-0.5 block text-xs leading-relaxed text-ink-300">{g.hint}</span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                      <Field label="Anything frustrating you right now?" hint="optional">
                        <textarea
                          className="input h-20 resize-none"
                          value={challenges}
                          onChange={(e) => setChallenges(e.target.value)}
                          placeholder="e.g. Customers tell us ChatGPT never mentions our shop…"
                          aria-label="Anything frustrating you right now"
                        />
                      </Field>
                    </div>
                  )}

                  {step === 2 && (
                    <div className="space-y-6">
                      <div>
                        <span className="field-label">Do you have a website?</span>
                        <div className="inline-flex rounded-xl border border-ink/10 bg-paper-200/60 p-1">
                          {[
                            { v: true, label: "Yes" },
                            { v: false, label: "Not yet" },
                          ].map(({ v, label }) => (
                            <button
                              key={label}
                              type="button"
                              aria-pressed={hasSite === v}
                              onClick={() => {
                                setHasSite(v);
                                if (!v) setLiveDepth("none");
                              }}
                              className={`rounded-lg px-5 py-2 text-sm transition-all duration-200 ${
                                hasSite === v ? "bg-paper-100 font-medium text-ink shadow-card" : "text-ink-300 hover:text-ink-500"
                              }`}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <Field label={hasSite ? "Your website address" : "Got a domain name picked out?"} hint={hasSite ? undefined : "optional"}>
                        <input
                          className="input"
                          value={domain}
                          onChange={(e) => setDomain(e.target.value)}
                          placeholder="yourbusiness.com"
                          inputMode="url"
                          aria-label="Website address"
                        />
                      </Field>

                      {hasSite && (
                        <fieldset className="space-y-2.5" role="radiogroup" aria-label="How deep should we look">
                          <legend className="field-label">How deep should we look?</legend>
                          <DepthOption
                            value="none"
                            current={liveDepth}
                            onPick={setLiveDepth}
                            title="Just plan my ideal website"
                            detail="Skip the check-up — build the plan from your answers alone."
                            badge="Instant"
                          />
                          <DepthOption
                            value="quick"
                            current={liveDepth}
                            onPick={setLiveDepth}
                            title="Quick look at my site"
                            detail="A fast scan so the plan reflects what you already have."
                            badge="~1 min"
                          />
                          <DepthOption
                            value="deep"
                            current={liveDepth}
                            onPick={setLiveDepth}
                            title="Full website check-up"
                            detail="Page-by-page review with scores — the most accurate plan."
                            badge="5–15 min"
                          />
                        </fieldset>
                      )}
                    </div>
                  )}

                  {step === 3 && (
                    <CompetitorPicker
                      businessName={name}
                      category={category}
                      location={location}
                      domain={domain}
                      selected={competitors}
                      onChange={setCompetitors}
                    />
                  )}

                  {step === 4 && (
                    <div className="space-y-6">
                      <ReviewSummary
                        rows={[
                          {
                            label: "Business",
                            value: [name.trim(), category.trim(), location.trim()].filter(Boolean).join(" · ") || "—",
                            jump: 0,
                          },
                          { label: "Goals", value: goals.join(", ") || "Improve AI visibility", jump: 1 },
                          {
                            label: "Website",
                            value: !hasSite
                              ? "Planning a new one"
                              : `${domain.trim() || "—"}${
                                  liveDepth === "deep" ? " · full check-up" : liveDepth === "quick" ? " · quick look" : ""
                                }`,
                            jump: 2,
                          },
                          {
                            label: "Competitors",
                            value: competitors.map((c) => c.name).join(", ") || "None — that's fine",
                            jump: 3,
                          },
                        ]}
                        onJump={setStep}
                      />

                      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-ink/10 bg-paper-200/50 px-4 py-3.5 text-sm">
                        <input
                          type="checkbox"
                          className="toggle"
                          checked={useLlm}
                          onChange={(e) => setUseLlm(e.target.checked)}
                        />
                        <span>
                          <span className="block font-medium text-ink">Personalize the wording with AI</span>
                          <span className="block text-xs text-ink-300">Recommended — takes a little longer but reads like it was written for you.</span>
                        </span>
                      </label>

                      <div>
                        <button
                          onClick={runAnalysis}
                          disabled={loading || !name.trim()}
                          className="btn-accent !px-6 !py-3 text-[15px]"
                        >
                          {loading ? (
                            <>
                              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                              {deepMode ? "Reviewing your website…" : "Building your plan…"}
                            </>
                          ) : analyzed ? (
                            "Rebuild my plan"
                          ) : (
                            "Create my plan"
                          )}
                        </button>
                        {!loading && (
                          <p className="mt-2 text-xs text-ink-300">
                            {!name.trim()
                              ? "Add your business name in step 1 first."
                              : deepMode
                                ? "The full check-up usually takes 5–15 minutes — you can leave this tab open."
                                : "Usually under a minute."}
                          </p>
                        )}
                      </div>

                      {auditJob && (auditJob.status === "queued" || auditJob.status === "running") && (
                        <AuditProgress job={auditJob} />
                      )}
                      {analyzed && !loading && (
                        <button onClick={() => setView("results")} className="btn-ghost text-[13px]">
                          View my results →
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-6 flex items-center justify-between gap-3">
                <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0} className="btn-ghost">
                  ← Back
                </button>
                <span className="label-mono">
                  {String(step + 1).padStart(2, "0")} / {STEPS.length}
                </span>
                {step < STEPS.length - 1 ? (
                  <div className="text-right">
                    <button onClick={() => setStep((s) => s + 1)} disabled={nextBlocker !== null} className="btn-primary">
                      Next →
                    </button>
                    {nextBlocker && <p className="mt-1.5 text-xs text-ink-300">{nextBlocker}</p>}
                  </div>
                ) : (
                  <span aria-hidden className="w-[72px]" />
                )}
              </div>
            </div>
          </div>
        )}
      </section>

      <TrustBand />
      <Footer />
    </>
  );
}

// ── wizard pieces ───────────────────────────────────────────────────────────

function ErrorNote({ message }: { message: string }) {
  return (
    <div className="step-in mb-6 flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
      <span aria-hidden className="mt-0.5">⚠</span>
      <span>
        <span className="font-medium">We hit a snag.</span> {message}
      </span>
    </div>
  );
}

function Stepper({ current, onJump }: { current: number; onJump: (i: number) => void }) {
  return (
    <nav aria-label="Steps" className="md:sticky md:top-24 md:self-start">
      <ol className="flex gap-1 overflow-x-auto pb-2 md:flex-col md:gap-0.5 md:overflow-visible md:pb-0">
        {STEPS.map(({ label }, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li key={label} className="shrink-0">
              <button
                onClick={() => onJump(i)}
                aria-current={active ? "step" : undefined}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-all duration-200 ${
                  active ? "bg-ink text-paper-100" : "text-ink-500 hover:bg-ink/[0.04]"
                }`}
              >
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[11px] transition-colors duration-200 ${
                    done ? "bg-accent text-white" : active ? "bg-paper-100 text-ink" : "border border-ink/15 text-ink-300"
                  }`}
                >
                  {done ? <Check className="animate-pop" width={12} height={12} /> : String(i + 1).padStart(2, "0")}
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
  return (
    <div className="mb-6 border-b border-ink/[0.06] pb-5">
      <span className="label-mono">Step {String(index + 1).padStart(2, "0")}</span>
      <h3 className="mt-1.5 text-xl font-semibold">{STEPS[index].label}</h3>
      <p className="mt-1 text-sm text-ink-500">{STEPS[index].blurb}</p>
    </div>
  );
}

// Deliberately a <div>, not a <label>: several fields wrap composite widgets
// (Select/Combobox popovers), and a wrapping label re-dispatches option clicks to the
// inner control. Inputs carry explicit aria-labels instead.
function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <span className="field-label">
        {label}
        {required && <span className="ml-1 text-accent">*</span>}
        {hint && <span className="ml-1.5 normal-case tracking-normal text-ink-300">({hint})</span>}
      </span>
      {children}
    </div>
  );
}

function DepthOption({
  value,
  current,
  onPick,
  title,
  detail,
  badge,
}: {
  value: "none" | "quick" | "deep";
  current: string;
  onPick: (v: "none" | "quick" | "deep") => void;
  title: string;
  detail: string;
  badge: string;
}) {
  const on = current === value;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={() => onPick(value)}
      className={`option-card !items-start justify-between ${on ? "option-card-on" : "option-card-off"}`}
    >
      <span className="flex items-start gap-3">
        <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${on ? "border-accent" : "border-ink/30"}`}>
          {on && <span className="h-2 w-2 animate-pop rounded-full bg-accent" />}
        </span>
        <span>
          <span className={`block font-medium ${on ? "text-ink" : ""}`}>{title}</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-ink-300">{detail}</span>
        </span>
      </span>
      <span className="label-mono shrink-0">{badge}</span>
    </button>
  );
}

function ReviewSummary({
  rows,
  onJump,
}: {
  rows: { label: string; value: string; jump: number }[];
  onJump: (step: number) => void;
}) {
  return (
    <dl className="divide-y divide-ink/[0.06] overflow-hidden rounded-xl border border-ink/[0.08]">
      {rows.map((r, i) => (
        <div key={r.label} className="step-in flex items-center gap-4 px-4 py-3" style={{ animationDelay: `${i * 60}ms` }}>
          <dt className="label-mono w-24 shrink-0">{r.label}</dt>
          <dd className="min-w-0 flex-1 truncate text-sm text-ink">{r.value}</dd>
          <button
            type="button"
            onClick={() => onJump(r.jump)}
            className="shrink-0 text-xs text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
          >
            Edit
          </button>
        </div>
      ))}
    </dl>
  );
}

function AuditProgress({ job }: { job: AuditJob }) {
  return (
    <div className="step-in rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
      <div className="flex items-center gap-2.5">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-60" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
        </span>
        <span className="font-medium">
          {job.status === "queued" ? "Getting ready to review your site…" : "Reviewing your website page by page…"}
        </span>
      </div>
      {job.progress && <p className="mt-1.5 font-mono text-xs opacity-70">{job.progress}</p>}
      <p className="mt-1.5 text-xs">This is the thorough option — grab a coffee, we'll keep working.</p>
    </div>
  );
}
