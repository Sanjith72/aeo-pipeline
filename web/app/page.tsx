"use client";

// AEO Studio — URL-first. The website is the only thing you must enter; a fast crawl
// derives your industry and location so you edit prefilled answers instead of typing
// them. Goals come after that crawl, then a comprehensive site analysis (with live
// progress) produces a phased, interactive plan. Chrome lives in components/chrome.tsx,
// the results experience in components/results.tsx.

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
import { GOAL_OPTIONS, INDUSTRIES, LOCATIONS } from "@/lib/options";
import { aeoScore } from "@/lib/score";
import { Faq, Footer, Hero, HowItWorks, SheetTag, TopBar, TrustBand } from "@/components/chrome";
import { AnalysisProgress, ResultsView, ScoreRing, triggerDownload } from "@/components/results";
import { CompetitorPicker } from "@/components/CompetitorPicker";
import { Combobox } from "@/components/ui/Combobox";
import { Check } from "@/components/ui/icons";

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

// A friendly default business name from a domain ("harbor-dental.com" → "Harbor Dental"),
// so the owner edits a sensible value rather than starting from an empty field.
function deriveName(domain: string): string {
  const host = domain
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./i, "")
    .split(/[/?#]/)[0];
  const root = host.split(".")[0] || host;
  return root
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// "data from N hours ago" — a friendly cache-age phrase for the re-crawl prompt (R2-2).
function formatAge(hours: number): string {
  if (hours < 1) return "the last few minutes";
  if (hours < 2) return "about an hour ago";
  if (hours < 24) return `${Math.round(hours)} hours ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

const STEPS = [
  { label: "Your website", blurb: "Pop in your address — we'll take a look." },
  { label: "About you", blurb: "We filled this in from your site. Fix anything that's off." },
  { label: "Competitors", blurb: "We found some likely ones — just tick the right names." },
  { label: "Your goals", blurb: "What would success look like? Then we'll build your plan." },
] as const;

export default function Page() {
  const [step, setStep] = useState(0);
  const [view, setView] = useState<"wizard" | "results">("wizard");

  // the brief — the website is the only required first input (#1)
  const [domain, setDomain] = useState("");
  const [hasSite, setHasSite] = useState(true);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [servicesText, setServicesText] = useState("");
  const [competitors, setCompetitors] = useState<CompetitorPick[]>([]);
  const [goals, setGoals] = useState<string[]>([]);
  const [challenges, setChallenges] = useState("");
  const [useLlm, setUseLlm] = useState(true);

  // crawl-derived intake (#2/#3): the fast profile that runs when leaving step 0
  const [prefilling, setPrefilling] = useState(false);
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null);

  // results
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<BriefPlan | null>(null);
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(null);
  const [delivLoading, setDelivLoading] = useState(false);
  const [auditJob, setAuditJob] = useState<AuditJob | null>(null);
  const [deepProfile, setDeepProfile] = useState<SiteProfile | null>(null);
  // R2-2 re-crawl: when the homepage was crawled recently, default to reusing that data
  // (fast) and let the user opt into a fresh re-crawl that bypasses the skip gate.
  const [forceRecrawl, setForceRecrawl] = useState(false);

  // Spec #1 (retention): the persisted, resumable plan. `planStateId` is the id behind the
  // shareable /plan/<id> link minted when the plan is generated; `resume` is a prior saved
  // plan for this browser, offered as a 'pick up where you left off' banner on return.
  const [planStateId, setPlanStateId] = useState<string | null>(null);
  const [resume, setResume] = useState<{ id: string; label: string } | null>(null);
  useEffect(() => {
    api
      .resumePlan()
      .then((r) => {
        if (r.id) setResume({ id: r.id, label: r.business_name || r.domain || "your plan" });
      })
      .catch(() => {});
  }, []);
  const auditJobIdRef = useRef<string | null>(null);

  const studioRef = useRef<HTMLElement>(null);

  // A dead crawl (or no site at all) routes to the no-website brief path (#3).
  const noSite = !hasSite || profileResult?.route === "dead";
  const profile: SiteProfile | null = deepProfile ?? profileResult?.profile ?? plan?.profile ?? null;
  const analyzed = profile !== null || plan !== null || auditJob?.status === "succeeded";

  // fire session_start / return_visit once on load (Block F instrumentation)
  useEffect(() => {
    api.trackVisit();
  }, []);

  // moving between steps always shows the top of the new step
  useEffect(() => {
    if (view !== "wizard") return;
    const top = studioRef.current?.getBoundingClientRect().top ?? 0;
    if (top < -40) studioRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [step, view]);

  function briefFromForm(): BriefRequest {
    return {
      name: name.trim() || deriveName(domain) || "My business",
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

  // Leaving step 0: take a fast look at the site so steps 1–3 come prefilled (#2). The
  // profile endpoint never 502s — a dead crawl returns route='dead' and we fall through
  // to manual entry / the no-website path (#3).
  async function handleWebsiteNext() {
    api.track("wizard_step_completed", { step: 0 });
    if (!hasSite || !domain.trim()) {
      setStep(1);
      return;
    }
    setPrefilling(true);
    setError(null);
    try {
      const res = await api.profile({ domain: domain.trim(), use_llm: useLlm });
      setProfileResult(res);
      if (res.industry && !category.trim()) setCategory(res.industry);
      if (res.location && !location.trim()) setLocation(res.location);
      // What you offer — prefilled from the crawl (schema.org offerings + service pages).
      if (res.services && res.services.length > 0 && !servicesText.trim()) {
        setServicesText(res.services.join(", "));
      }
      // Seed competitors found on-site so they're already ticked when the user reaches
      // the competitor step (they can untick/add). The picker still fetches more.
      if (res.competitors && res.competitors.length > 0 && competitors.length === 0) {
        setCompetitors(
          res.competitors.map((c) => ({ name: c.name, domain: c.domain || undefined, source: "suggested" as const })),
        );
      }
      if (!name.trim()) setName(deriveName(domain));
    } catch (err) {
      // network failure only — let the user continue with manual entry
      setError(err instanceof Error ? err.message : String(err));
      if (!name.trim()) setName(deriveName(domain));
    } finally {
      setPrefilling(false);
      setStep(1);
    }
  }

  function advance(fromStep: number) {
    api.track("wizard_step_completed", { step: fromStep });
    setStep((s) => s + 1);
  }

  // Capture any prefilled value the user edited away from what the crawl inferred (R2-4):
  // each edit is logged as eval signal AND a human-gated PROPOSED refinement (never
  // auto-applied). The LLM does the inferring; the human only validates/overrides.
  function captureIntakeOverrides() {
    const inf = profileResult;
    if (!inf) return;
    const cat = category.trim();
    const loc = location.trim();
    if (inf.industry && cat && cat !== inf.industry) {
      api.captureOverride("industry", inf.industry, cat, "field_override", domain.trim() || undefined);
      api.trackOverride("industry", inf.industry, cat, { source: "intake" }); // Task-7 eval stream
    }
    if (inf.location && loc && loc !== inf.location) {
      api.captureOverride("location", inf.location, loc, "field_override", domain.trim() || undefined);
      api.trackOverride("location", inf.location, loc, { source: "intake" }); // Task-7 eval stream
    }
  }

  // Final action: run the comprehensive analysis (always — no mode to choose, #6), then
  // show results. A site gets the full page-by-page audit with live progress (#7); a
  // no-website brief gets the instant blueprint.
  async function createPlan() {
    api.track("wizard_step_completed", { step: 3 });
    captureIntakeOverrides();
    setError(null);
    setPlan(null);
    setDeliverables(null);
    setAuditJob(null);
    setDeepProfile(null);
    setLoading(true);
    try {
      if (noSite) {
        setPlan(await api.plan(briefFromForm()));
      } else {
        const ok = await runDeepAudit();
        if (!ok) return; // audit failed — error already surfaced
      }
      setView("results");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function cancelAudit() {
    const jobId = auditJobIdRef.current;
    if (!jobId) return;
    try {
      setAuditJob(await api.cancelAudit(jobId));
    } catch {
      /* best-effort — the poll loop still resolves the run to a terminal state */
    }
  }

  async function runDeepAudit(): Promise<boolean> {
    const { job_id } = await api.startAudit({
      domain: domain.trim(),
      name: name.trim() || deriveName(domain),
      force: forceRecrawl,
    });
    auditJobIdRef.current = job_id;
    let job = await api.auditStatus(job_id);
    setAuditJob(job);
    let tries = 0;
    while ((job.status === "queued" || job.status === "running") && tries < 450) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      job = await api.auditStatus(job_id);
      setAuditJob(job);
      tries += 1;
    }
    if (job.status === "failed") {
      // fall back to the fast profile we already have rather than dead-ending
      if (!profileResult?.profile) {
        setError(job.error || "We couldn't finish reviewing your site. Please try again.");
        return false;
      }
      return true;
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
      const deliv = await api.deliverables({ ...briefFromForm(), draft_limit: 10 });
      setDeliverables(deliv);
      // Spec #1: persist the interactive plan so progress survives a device switch and
      // earns a resumable /plan/<id> link. Best-effort — the in-app plan works without it.
      if (deliv.plan && !planStateId) {
        const prof = deepProfile ?? profileResult?.profile ?? plan?.profile ?? null;
        try {
          const { id } = await api.createPlanState({
            plan: deliv.plan,
            profile: prof,
            business_name: name.trim() || null,
            domain: domain.trim() || null,
            score: prof ? aeoScore(prof) : null,
          });
          setPlanStateId(id);
        } catch {
          /* best-effort — no shareable link, but the plan still works in-app */
        }
      }
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
    if (step === 0 && hasSite && !domain.trim()) return "Add your website address, or pick “I don't have a site yet”";
    if (step === 1 && !name.trim()) return "Add your business name to continue";
    return null;
  })();

  const isLast = step === STEPS.length - 1;

  return (
    <>
      <TopBar />
      <Hero />
      <HowItWorks />

      <section id="studio" ref={studioRef} className="mx-auto max-w-6xl scroll-mt-20 px-5 py-16 sm:py-20">
        <div className="mb-8 animate-fade-up">
          <SheetTag no="03">Your plan builder</SheetTag>
          <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">
            {view === "results" ? "Your results" : "Start with your website. We'll do the rest."}
          </h2>
          {view === "wizard" && (
            <p className="mt-1 max-w-2xl text-ink-500">
              Enter your address and we'll figure out your industry, your gaps, and a step-by-step plan
              to make your business the one AI recommends.
            </p>
          )}
        </div>

        {view === "wizard" && resume && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-accent/30 bg-accent/[0.06] p-4 animate-fade-up">
            <p className="text-sm text-ink-500">
              Welcome back — you have a saved plan for{" "}
              <span className="font-medium text-ink">{resume.label}</span>.
            </p>
            <a href={`/plan/${resume.id}`} className="btn-ghost shrink-0 text-[13px]">
              Resume your plan →
            </a>
          </div>
        )}

        {view === "results" ? (
          <>
            {error && <ErrorNote message={error} />}
            {planStateId && (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-4 py-3">
                <p className="text-sm text-ink-500">
                  Your plan is saved — open it on any device, or come back to this link later.
                </p>
                <a href={`/plan/${planStateId}`} className="btn-ghost shrink-0 text-[13px]">
                  Open shareable link →
                </a>
              </div>
            )}
            <ResultsView
              businessName={name.trim()}
              domain={hasSite ? domain.trim() || undefined : undefined}
              profile={profile}
              plan={plan}
              auditJob={auditJob}
              deliverables={deliverables}
              delivLoading={delivLoading}
              aiPersonalization={useLlm}
              cmsType={profileResult?.cms_type ?? null}
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
                              onClick={() => setHasSite(v)}
                              className={`rounded-lg px-5 py-2 text-sm transition-all duration-200 ${
                                hasSite === v ? "bg-paper-100 font-medium text-ink shadow-card" : "text-ink-300 hover:text-ink-500"
                              }`}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <Field
                        label={hasSite ? "Your website address" : "Got a domain name picked out?"}
                        hint={hasSite ? undefined : "optional"}
                        required={hasSite}
                      >
                        <input
                          className="input"
                          value={domain}
                          onChange={(e) => setDomain(e.target.value)}
                          placeholder="yourbusiness.com"
                          inputMode="url"
                          autoFocus
                          aria-label="Website address"
                        />
                      </Field>

                      <p className="text-xs text-ink-300">
                        {hasSite
                          ? "We take a quick look and show your AI visibility score in seconds — then pre-fill the next steps for you."
                          : "No website yet? No problem — we'll plan your ideal one from scratch."}
                      </p>
                    </div>
                  )}

                  {step === 1 && (
                    <div className="space-y-5">
                      {/* Critical #1: the score lands the instant the fast crawl finishes — a
                          credit-score moment on step 1, not gated behind the 5–15 min audit. */}
                      {profileResult?.profile && !noSite && (
                        <ScoreRing profile={profileResult.profile} provisional className="step-in" />
                      )}
                      {profileResult && profileResult.route !== "dead" && (
                        <p className="step-in rounded-lg border border-emerald-500/25 bg-emerald-500/[0.08] px-3.5 py-2.5 text-sm text-emerald-300">
                          <Check className="mr-1.5 inline" width={13} height={13} />
                          We looked at <span className="font-mono">{domain.trim()}</span> and filled in what we
                          could. Edit anything below.
                        </p>
                      )}
                      {noSite && hasSite && (
                        <p className="step-in rounded-lg border border-amber-500/25 bg-amber-500/[0.08] px-3.5 py-2.5 text-sm text-amber-200">
                          We couldn't read much from that address, so we'll plan from your answers. Fill in
                          the basics below.
                        </p>
                      )}
                      {!noSite && profileResult?.cache_age_hours != null && (
                        <label className="step-in flex cursor-pointer items-start gap-3 rounded-lg border border-ink/10 bg-paper-200/50 px-3.5 py-2.5 text-sm">
                          <input
                            type="checkbox"
                            className="toggle mt-0.5"
                            checked={forceRecrawl}
                            onChange={(e) => setForceRecrawl(e.target.checked)}
                          />
                          <span>
                            <span className="block font-medium text-ink">Re-crawl my site from scratch</span>
                            <span className="block text-xs text-ink-300">
                              We have data from {formatAge(profileResult.cache_age_hours)} — leave this off to
                              reuse it (faster), or turn it on to read every page fresh.
                            </span>
                          </span>
                        </label>
                      )}
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

                  {step === 2 && (
                    <CompetitorPicker
                      businessName={name}
                      category={category}
                      location={location}
                      domain={domain}
                      services={splitList(servicesText)}
                      selected={competitors}
                      onChange={setCompetitors}
                    />
                  )}

                  {step === 3 && (
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

                      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-ink/10 bg-paper-200/50 px-4 py-3.5 text-sm">
                        <input
                          type="checkbox"
                          className="toggle"
                          checked={useLlm}
                          onChange={(e) => setUseLlm(e.target.checked)}
                        />
                        <span>
                          <span className="block font-medium text-ink">Personalize the wording with AI</span>
                          <span className="block text-xs text-ink-300">
                            Recommended — reads like it was written for you. Adds a build step (around 10 minutes)
                            after the review; leave it off for an instant plan.
                          </span>
                        </span>
                      </label>

                      <div>
                        <button onClick={createPlan} disabled={loading} className="btn-accent !px-6 !py-3 text-[15px]">
                          {loading ? (
                            <>
                              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                              {noSite ? "Building your plan…" : "Reviewing your website…"}
                            </>
                          ) : analyzed ? (
                            "Rebuild my plan"
                          ) : (
                            "Create my plan"
                          )}
                        </button>
                        {!loading && (
                          <p className="mt-2 text-xs text-ink-300">
                            {noSite
                              ? "Usually under a minute."
                              : "You already have your score — this is the full page-by-page review, usually around 10 minutes. You'll see progress as it goes, and you can leave this tab open."}
                          </p>
                        )}
                      </div>

                      {loading && !noSite && auditJob && <AnalysisProgress job={auditJob} onCancel={cancelAudit} />}
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
                {!isLast ? (
                  <div className="flex items-center gap-3">
                    {/* After the URL+crawl, every later step is prefilled/optional — let the
                        user bail straight to the comprehensive analysis (#1: URL is enough). */}
                    {step >= 1 && (
                      <button
                        onClick={() => {
                          setStep(STEPS.length - 1); // land on the step that shows live progress
                          createPlan();
                        }}
                        disabled={loading || prefilling}
                        className="btn-ghost"
                        title="Skip the rest — analyze your site with what we already have"
                      >
                        Skip — just analyze my site
                      </button>
                    )}
                    <div className="text-right">
                      <button
                        onClick={() => (step === 0 ? handleWebsiteNext() : advance(step))}
                        disabled={nextBlocker !== null || prefilling}
                        className="btn-primary group"
                      >
                        {prefilling ? (
                          <>
                            <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/30 border-t-ink" />
                            Taking a look…
                          </>
                        ) : (
                          <>
                            Next
                            <span aria-hidden className="transition-transform duration-200 group-hover:translate-x-0.5">→</span>
                          </>
                        )}
                      </button>
                      {nextBlocker && <p className="mt-1.5 text-xs text-ink-300">{nextBlocker}</p>}
                    </div>
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
      <Faq />
      <Footer />
    </>
  );
}

// ── wizard pieces ───────────────────────────────────────────────────────────

function ErrorNote({ message }: { message: string }) {
  return (
    <div className="step-in mb-6 flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
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
                  active ? "bg-ink text-paper-100 shadow-card" : "text-ink-500 hover:translate-x-0.5 hover:bg-ink/[0.04]"
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
// (Combobox popovers), and a wrapping label re-dispatches option clicks to the inner
// control. Inputs carry explicit aria-labels instead.
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
    <div className="field-group">
      <span className="field-label">
        {label}
        {required && <span className="ml-1 text-accent">*</span>}
        {hint && <span className="ml-1.5 normal-case tracking-normal text-ink-300">({hint})</span>}
      </span>
      {children}
    </div>
  );
}
