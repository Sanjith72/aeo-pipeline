"use client";

// The AEO Studio product — URL-first. The website is the only thing you must enter; a fast
// crawl derives your industry and location so you edit prefilled answers instead of typing
// them. Goals come after that crawl, then a comprehensive site analysis (with live
// progress) produces a phased, interactive plan. Rendered on /studio (marketing lives on
// the root route); the results experience is in components/results.tsx.

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  AuditJob,
  BriefPlan,
  BriefRequest,
  CompetitorPick,
  DeliverablesJob,
  DeliverablesResponse,
  PackPreview,
  ProfileResponse,
  SiteProfile,
} from "@/lib/types";
import { GOAL_OPTIONS, INDUSTRIES, LOCATIONS } from "@/lib/options";
import { aeoScore } from "@/lib/score";
import { DisplayH2, SheetTag } from "@/components/chrome";
import { AnalysisProgress, PrefillProgress, ResultsView, ScoreRing, triggerDownload } from "@/components/results";
import { CompetitorPicker } from "@/components/CompetitorPicker";
import { PackCard } from "@/components/PackCard";
import { useAuth } from "@/components/auth/AuthProvider";
import { UnlockModal } from "@/components/auth/UnlockModal";
import {
  POLL_DELAYS_MS,
  clearPendingCheckout,
  isPackUnlocked,
  readCheckoutOutcome,
  readPendingCheckout,
  urlWithoutCheckoutParams,
} from "@/lib/checkoutReturn";
import { GamificationStrip } from "@/components/GamificationStrip";
import { Combobox } from "@/components/ui/Combobox";
import { LiquidButton } from "@/components/ui/liquid-glass";
import { Reveal, useReducedMotion } from "@/components/motion/primitives";
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

// #2 — the staged analysis experience shown while the fast crawl runs. The crawl is one call
// that returns in seconds; these stages animate the work so the wait reads as the AI actively
// examining the site (Perplexity/Linear-style) instead of a silent spinner.
const ANALYSIS_STAGES = [
  "Checking your website structure",
  "Discovering your pages",
  "Evaluating AEO readiness",
  "Analyzing content quality",
  "Identifying authority signals",
  "Spotting optimization opportunities",
  "Building your strategic roadmap",
] as const;

// #3 — goals the analysis recommends pre-selected. The crawl already reveals the gaps, the
// business model, and whether the business is local, so we tick the goals that map to those
// findings; the user unticks or adds their own. Outcome language only — keys match GOAL_OPTIONS.
function recommendedGoals(profile: SiteProfile | null, hasLocation: boolean): Set<string> {
  const rec = new Set<string>(["Show up in AI answers"]); // the core promise — always relevant
  if (!profile) {
    rec.add("Win more customers");
    return rec;
  }
  const stages = profile.journey?.stages ?? [];
  const covered = stages.filter((s) => s.covered).length;
  const model = (profile.business_intent?.model ?? "").toLowerCase();
  if (stages.length > 0 && covered / stages.length < 0.6) rec.add("Build my brand's authority");
  if ((profile.journey?.gaps ?? []).length > 0) rec.add("Win more customers");
  if (/commerce|retail|shop|product|store/.test(model)) rec.add("Sell more online");
  if (hasLocation || /local|service/.test(model)) rec.add("Grow local business");
  if ((profile.actions ?? []).length > 3) rec.add("Beat my competitors");
  return rec;
}

// Step blurbs come from the design handoff — plain-English promises, one per panel.
const STEPS = [
  {
    label: "Your website",
    blurb: "We’ll scan it the way an AI assistant does — structure, answers, credibility signals.",
  },
  { label: "About you", blurb: "A few basics so the plan sounds like you, not a template." },
  {
    label: "Competitors",
    blurb: "After your website scan we’ll suggest competitors from your industry — keep the ones that fit, add any we missed.",
  },
  { label: "Your goals", blurb: "What would success look like? Then we’ll build your plan." },
] as const;

export function StudioApp() {
  const [step, setStep] = useState(0);
  const [view, setView] = useState<"wizard" | "results">("wizard");
  // The overview's "Go deeper" handoff (?review=1): every intake section — website, about
  // you, competitors, goals — prefilled and stacked on ONE page instead of the four-step
  // wizard. "Edit" from results returns to whichever layout the user came through.
  const [onePage, setOnePage] = useState(false);

  // the brief — the website is the only required first input (#1)
  const [domain, setDomain] = useState("");
  const [hasSite, setHasSite] = useState(true);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [location, setLocation] = useState("");
  const [servicesText, setServicesText] = useState("");
  const [competitors, setCompetitors] = useState<CompetitorPick[]>([]);
  const [goals, setGoals] = useState<string[]>([]);
  const [customGoalInput, setCustomGoalInput] = useState("");
  const [challenges, setChallenges] = useState("");
  const [useLlm, setUseLlm] = useState(true);

  // crawl-derived intake (#2/#3): the fast profile that runs when leaving step 0
  const [prefilling, setPrefilling] = useState(false);
  // Flips true the moment the profile lands so PrefillProgress can snap to 100% for a beat
  // before we advance to step 1 (closure instead of a bar vanishing mid-climb).
  const [prefillDone, setPrefillDone] = useState(false);
  const [profileResult, setProfileResult] = useState<ProfileResponse | null>(null);

  // results
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<BriefPlan | null>(null);
  const [deliverables, setDeliverables] = useState<DeliverablesResponse | null>(null);
  const [delivLoading, setDelivLoading] = useState(false);
  const [delivError, setDelivError] = useState<string | null>(null);
  // #7 — the async "personalize my downloadable files" job. The in-app plan is always
  // instant; this optional upgrade rewrites the downloadable page drafts with the LLM and
  // runs as a background job so the slow build never blocks (or times out) the request.
  const [personalizing, setPersonalizing] = useState(false);
  const [personalizeError, setPersonalizeError] = useState<string | null>(null);
  const [personalizeJob, setPersonalizeJob] = useState<DeliverablesJob | null>(null);
  const [auditJob, setAuditJob] = useState<AuditJob | null>(null);
  const [deepProfile, setDeepProfile] = useState<SiteProfile | null>(null);
  // v5 CH-03: the impact-ordered packs the deep audit persisted (fetched beside the site
  // report). Best-effort — a run without persisted packs just shows nothing here.
  const [packs, setPacks] = useState<PackPreview[]>([]);
  const [runId, setRunId] = useState<number | null>(null);
  const [unlockOpen, setUnlockOpen] = useState(false);
  // Which pack the unlock dialog is for (v5 CH-02b) — drives the per-pack Stripe checkout.
  const [unlockPack, setUnlockPack] = useState<number | null>(null);
  const [openPack, setOpenPack] = useState<number | null>(null);
  // v5 CH-02b: the state of a Stripe return, if we are in one. Null = an ordinary visit.
  const [checkout, setCheckout] = useState<{
    state: "confirming" | "unlocked" | "pending_grant" | "cancelled" | "unknown_run";
    packIndex?: number;
  } | null>(null);
  const { authEnabled, user, openAuth } = useAuth();
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
  // Deep-link entries, read once then stripped (so a refresh doesn't re-run them):
  //   • autobuild=1 — a saved plan's "Build a plan for your site" link; runs the full
  //     crawl→audit unattended (contract: docs/prompts/route-split-studio.md).
  //   • review=1 — the overview's "Go deeper" CTA; prefills from the fast crawl, then
  //     shows the one-page review so the user sees everything before the audit runs.
  const autoBuildStarted = useRef(false);
  useEffect(() => {
    if (autoBuildStarted.current || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const mode =
      params.get("review") === "1" ? "review" : params.get("autobuild") === "1" ? "autobuild" : null;
    const target = params.get("domain")?.trim();
    if (!mode || !target) return;
    autoBuildStarted.current = true;
    const nameVal = params.get("name")?.trim() || "";
    window.history.replaceState(null, "", window.location.pathname + window.location.hash);
    if (mode === "review") void startReview(target);
    else void autoBuild(target, nameVal);
    // Both entry functions are stable closures over component state setters; run once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const auditJobIdRef = useRef<string | null>(null);
  // The domain string we last ran the prefill crawl for — so we can detect a site change
  // (Back-and-edit, or just typing a new URL) and drop stale prefills before re-crawling.
  const lastProfiledDomainRef = useRef<string | null>(null);
  const personalizeJobIdRef = useRef<string | null>(null);
  // Seeds recommended goals once when the user first reaches step 3; reset on a site change
  // (resetPrefilled) so a new site re-suggests.
  const goalsSeeded = useRef(false);

  // Drop every crawl-derived "About you"/competitor/goal prefill and the inferred snapshot
  // used for override tracking, so a new site never inherits the previous one's answers.
  // The typed domain is deliberately left alone (the user is editing it).
  function resetPrefilled() {
    setName("");
    setCategory("");
    setLocation("");
    setServicesText("");
    setGoals([]);
    setCompetitors([]);
    setProfileResult(null);
    setForceRecrawl(false);
    // A fresh site gets a fresh goal suggestion.
    goalsSeeded.current = false;
  }

  // A dead crawl (or no site at all) routes to the no-website brief path (#3).
  const noSite = !hasSite || profileResult?.route === "dead";
  const profile: SiteProfile | null = deepProfile ?? profileResult?.profile ?? plan?.profile ?? null;
  const analyzed = profile !== null || plan !== null || auditJob?.status === "succeeded";
  // Stricter than `analyzed` for the one-pager's CTA: the fast prefill crawl alone sets a
  // profile (so `analyzed` is true the moment the review page lands), but nothing has been
  // BUILT yet — "Rebuild"/"View my results" must wait for a real plan or deep audit.
  const hasRunResults = plan !== null || deepProfile !== null || auditJob?.status === "succeeded";

  // fire session_start / return_visit once on load (Block F instrumentation)
  useEffect(() => {
    api.trackVisit();
  }, []);

  // v5 CH-02a: when the user signs in (or out), re-fetch the current run's packs so the
  // per-user `locked` flags recompute — an entitled user's deeper packs unlock without a
  // page reload. Runs only once packs exist for a run.
  useEffect(() => {
    if (runId == null) return;
    void refreshPacks();
    // refreshPacks is a stable closure over runId; re-run when the signed-in user changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // moving between steps always shows the top of the new step
  useEffect(() => {
    if (view !== "wizard") return;
    if (window.scrollY > 40) window.scrollTo({ top: 0, behavior: "smooth" });
  }, [step, view]);

  // #3 — when the user first lands on the goals step, pre-select the goals the analysis
  // recommends (once). They can untick or add their own afterward.
  useEffect(() => {
    if (goalsSeeded.current || step !== 3) return;
    goalsSeeded.current = true;
    if (goals.length === 0) setGoals([...recommendedGoals(profile, !!location.trim())]);
  }, [step, profile, location, goals.length]);

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

  // #3 — add a free-typed custom goal (e.g. "Rank for niche topics"). Stored alongside the
  // preset goals; rendered as a removable chip below the recommended grid.
  function addCustomGoal() {
    const g = customGoalInput.trim();
    if (!g) return;
    setGoals((prev) => (prev.includes(g) ? prev : [...prev, g]));
    setCustomGoalInput("");
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
    const target = domain.trim();
    // Site changed since the last crawl (even without using Back) → clear site A's prefills
    // so its industry/location/services/competitors never leak into site B.
    if (lastProfiledDomainRef.current !== null && lastProfiledDomainRef.current !== target) {
      resetPrefilled();
    }
    lastProfiledDomainRef.current = target;
    setPrefilling(true);
    setPrefillDone(false);
    setError(null);
    try {
      const res = await api.profile({ domain: target, use_llm: useLlm });
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
      // Snap the progress bar to 100% and let it read for a beat before advancing — closure
      // rather than a bar that vanishes mid-climb.
      setPrefillDone(true);
      await new Promise((resolve) => setTimeout(resolve, 450));
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
    // A stale build error from a previous run would suppress PlanPanel's autobuild
    // (its effect is gated on !error), so the new plan must start with a clean slate.
    setDelivError(null);
    setAuditJob(null);
    setDeepProfile(null);
    setPacks([]); // clear a prior run's packs so they never leak into this build (incl. no-site)
    setRunId(null);
    setOpenPack(null);
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

  // domainArg/nameArg let the auto-build path (deep-link from a saved plan) drive the audit
  // with explicit values, since its own setState calls aren't yet committed in this closure.
  async function runDeepAudit(domainArg?: string, nameArg?: string): Promise<boolean> {
    const d = (domainArg ?? domain).trim();
    const n = (nameArg ?? name).trim() || deriveName(d);
    const { job_id } = await api.startAudit({
      domain: d,
      name: n,
      force: forceRecrawl,
    });
    auditJobIdRef.current = job_id;
    let job = await api.auditStatus(job_id);
    setAuditJob(job);
    let tries = 0;
    let pollMisses = 0;
    while ((job.status === "queued" || job.status === "running") && tries < 450) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      tries += 1;
      try {
        job = await api.auditStatus(job_id);
      } catch (err) {
        // The audit keeps running server-side when a status poll drops (small hosts
        // hiccup under crawl load), so one miss must not fail the whole run — only
        // give up after ~30s of consecutive misses (the backend genuinely gone).
        pollMisses += 1;
        if (pollMisses >= 15) throw err;
        continue;
      }
      pollMisses = 0;
      setAuditJob(job);
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
      // v5 CH-03: pull the packs the audit persisted (impact-ordered, homepage = Pack 1).
      try {
        const p = await api.getPacks(runId);
        setPacks(p.packs);
        setRunId(runId);
      } catch {
        /* best-effort — a run without persisted packs just renders no pack section */
      }
    }
    return true;
  }

  // v5 CH-02a/b: clicking "Unlock" on a locked pack. Anonymous → sign in first; a logged-in
  // user gets the unlock dialog (buy this pack, or redeem a promo code). We remember WHICH
  // pack was clicked so checkout charges for that one — the dialog can't guess it.
  function handleUnlock(packIndex?: number) {
    if (!user) {
      openAuth("unlock-pack");
      return;
    }
    setUnlockPack(packIndex ?? null);
    setUnlockOpen(true);
  }
  async function refreshPacks() {
    if (runId == null) return;
    try {
      const p = await api.getPacks(runId);
      setPacks(p.packs);
    } catch {
      /* best-effort */
    }
  }

  // ── v5 CH-02b: the checkout RETURN leg ────────────────────────────────────────
  // Stripe sends the buyer to `/studio?checkout=success`, and NOTHING here read it: they
  // landed on the studio's normal empty state — no confirmation, no run, no pack. A
  // successful payment was indistinguishable from a failed one.
  //
  // Two things have to happen, in this order:
  //   1. Restore context. The return is a full page load (often a new tab), so the run is
  //      gone. It comes back from localStorage, or from the pack/run_id we appended to
  //      success_url when the storage is not this browser's.
  //   2. Wait for the GRANT. The entitlement is written by the webhook, not by this
  //      redirect, and a fast return regularly beats it — so poll with backoff rather than
  //      reading once and telling a paying customer their pack is still locked.
  const checkoutHandled = useRef(false);
  useEffect(() => {
    if (checkoutHandled.current || typeof window === "undefined") return;
    const outcome = readCheckoutOutcome(window.location.search);
    if (outcome.kind === "none") return;
    checkoutHandled.current = true;

    // Strip the params immediately so a refresh cannot re-trigger the flow.
    window.history.replaceState(
      null, "",
      urlWithoutCheckoutParams(window.location.pathname, window.location.search, window.location.hash),
    );

    const pending = readPendingCheckout();
    if (outcome.kind === "cancelled") {
      clearPendingCheckout();
      setCheckout({ state: "cancelled" });
      return;
    }

    // Prefer the URL (survives a different tab/device) and fall back to storage.
    const boughtPack = outcome.packIndex ?? pending?.packIndex;
    const boughtRun = outcome.runId ?? pending?.runId;
    const boughtDomain = pending?.domain;
    if (boughtDomain && !domain.trim()) {
      setDomain(boughtDomain);
      setHasSite(true);
    }
    setCheckout({ state: "confirming", packIndex: boughtPack });

    let cancelled = false;
    void (async () => {
      const targetRun = boughtRun ?? runId ?? null;
      if (targetRun == null) {
        // Paid, but we cannot tell which run to reload — never pretend otherwise.
        setCheckout({ state: "unknown_run", packIndex: boughtPack });
        clearPendingCheckout();
        return;
      }
      setRunId(targetRun);
      for (let i = 0; i <= POLL_DELAYS_MS.length; i++) {
        if (cancelled) return;
        try {
          const p = await api.getPacks(targetRun);
          if (cancelled) return;
          setPacks(p.packs);
          if (boughtPack == null || isPackUnlocked(p.packs, boughtPack)) {
            setCheckout({ state: "unlocked", packIndex: boughtPack });
            if (boughtPack != null) setOpenPack(boughtPack);
            clearPendingCheckout();
            return;
          }
        } catch {
          /* keep polling — a transient failure is not an answer */
        }
        if (i < POLL_DELAYS_MS.length) {
          await new Promise((r) => setTimeout(r, POLL_DELAYS_MS[i]));
        }
      }
      // The webhook has not landed inside the window. Say so plainly and offer a retry —
      // the money is taken and the grant will almost certainly arrive; a silent blank or a
      // "still locked" pack would both be lies.
      if (!cancelled) setCheckout({ state: "pending_grant", packIndex: boughtPack });
    })();
    return () => {
      cancelled = true;
    };
    // Runs once on mount; deliberately not re-run when domain/runId change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Re-check entitlements on demand, behind the "Refresh" in the pending-grant notice. */
  async function recheckCheckout(packIndex?: number) {
    if (runId == null) return;
    setCheckout((c) => (c ? { ...c, state: "confirming" } : c));
    try {
      const p = await api.getPacks(runId);
      setPacks(p.packs);
      if (packIndex == null || isPackUnlocked(p.packs, packIndex)) {
        setCheckout({ state: "unlocked", packIndex });
        if (packIndex != null) setOpenPack(packIndex);
        clearPendingCheckout();
        return;
      }
    } catch {
      /* fall through to the honest pending state */
    }
    setCheckout({ state: "pending_grant", packIndex });
  }

  // One-click build from a saved plan's "Build a plan for your site" link. Mirrors the manual
  // path (fast crawl → comprehensive audit) but runs unattended: prefill is best-effort and we
  // jump straight to the live-progress audit. A dead crawl falls back to the instant brief.
  async function autoBuild(target: string, nameVal: string) {
    setHasSite(true);
    setDomain(target);
    if (nameVal) setName(nameVal);
    lastProfiledDomainRef.current = target;
    setError(null);
    setPlan(null);
    setDeliverables(null);
    setDelivError(null); // same clean slate as createPlan — see the note there
    setAuditJob(null);
    setDeepProfile(null);
    setPacks([]); // clear a prior run's packs before the unattended build
    setRunId(null);
    setOpenPack(null);

    setPrefilling(true);
    setPrefillDone(false);
    let res: ProfileResponse | null = null;
    // Competitors auto-selected for the unattended build. The picker is skipped on this
    // path, so we tick them here instead of letting the user choose.
    let competitorPicks: CompetitorPick[] = [];
    try {
      res = await api.profile({ domain: target, use_llm: useLlm });
      setProfileResult(res);
      if (res.industry) setCategory(res.industry);
      if (res.location) setLocation(res.location);
      if (res.services && res.services.length > 0) setServicesText(res.services.join(", "));
      // Prefer competitors mined from the user's own site (strongest signal). When the
      // crawl names none, fall back to the same recommendation call the CompetitorPicker
      // makes so the autobuild still compares against real industry peers — matching what
      // the user would have ticked had they walked the wizard.
      if (res.competitors && res.competitors.length > 0) {
        competitorPicks = res.competitors.map((c) => ({
          name: c.name,
          domain: c.domain || undefined,
          source: "suggested" as const,
        }));
      } else {
        try {
          const sug = await api.suggestCompetitors({
            name: (nameVal || deriveName(target)).trim(),
            domain: target,
            category: res.industry || undefined,
            location: res.location || undefined,
            services: res.services && res.services.length > 0 ? res.services : undefined,
            count: 8,
          });
          competitorPicks = sug.competitors.map((c) => ({
            name: c.name,
            domain: c.domain || undefined,
            source: "suggested" as const,
          }));
        } catch {
          /* recommendations are best-effort — the build still runs without competitors */
        }
      }
      if (competitorPicks.length > 0) setCompetitors(competitorPicks);
      if (!nameVal) setName(deriveName(target));
      setPrefillDone(true);
      await new Promise((resolve) => setTimeout(resolve, 350));
    } catch {
      /* network-only failure — continue to the audit, which can still read the site */
    } finally {
      setPrefilling(false);
    }

    // Land on the final step so the live audit progress (AnalysisProgress) is what renders.
    setStep(STEPS.length - 1);
    setLoading(true);
    try {
      if (res?.route === "dead") {
        // No readable site — the instant brief is the best we can do.
        setPlan(
          await api.plan({
            name: nameVal || deriveName(target),
            domain: target,
            category: res.industry || undefined,
            location: res.location || undefined,
            services: res.services ?? [],
            competitors: competitorPicks.map((c) => c.domain?.trim() || c.name),
            goals: [],
            use_llm: useLlm,
          }),
        );
      } else {
        const ok = await runDeepAudit(target, nameVal);
        if (!ok) return; // audit failed — error already surfaced
      }
      setView("results");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  // The overview's "Go deeper" entry (?review=1), and the one-pager's own re-scan button:
  // run the same fast prefill crawl the wizard does on leaving step 0, seed the recommended
  // goals immediately (the one-pager shows the goals section right away, so it can't wait
  // for the wizard's "reached step 3" effect), then land on the one-page review. The deep
  // audit still only starts on "Build my plan".
  async function startReview(target: string) {
    setOnePage(true);
    setView("wizard");
    setHasSite(true);
    setDomain(target);
    // A different site than the last crawl → drop its prefills so nothing leaks over.
    if (lastProfiledDomainRef.current !== null && lastProfiledDomainRef.current !== target) {
      resetPrefilled();
    }
    lastProfiledDomainRef.current = target;
    setError(null);
    setPrefilling(true);
    setPrefillDone(false);
    try {
      const res = await api.profile({ domain: target, use_llm: useLlm });
      setProfileResult(res);
      if (res.industry) setCategory(res.industry);
      if (res.location) setLocation(res.location);
      if (res.services && res.services.length > 0) setServicesText(res.services.join(", "));
      if (res.competitors && res.competitors.length > 0) {
        setCompetitors(
          res.competitors.map((c) => ({ name: c.name, domain: c.domain || undefined, source: "suggested" as const })),
        );
      }
      // Functional updates: resetPrefilled()'s clears are still queued in this batch, so
      // reading the closure's `name`/`goals` here would see the pre-reset values.
      setName((n) => (n.trim() ? n : deriveName(target)));
      goalsSeeded.current = true;
      setGoals((prev) =>
        prev.length > 0 ? prev : [...recommendedGoals(res.profile ?? null, !!(res.location ?? "").trim())],
      );
      setPrefillDone(true);
      await new Promise((resolve) => setTimeout(resolve, 450));
    } catch (err) {
      // network failure only — the one-pager still renders for manual entry
      setError(err instanceof Error ? err.message : String(err));
      setName((n) => (n.trim() ? n : deriveName(target)));
    } finally {
      setPrefilling(false);
    }
  }

  async function generateDeliverables() {
    setDelivLoading(true);
    setDelivError(null);
    try {
      // #7: the in-app plan is deterministic, so force the FAST path (use_llm=false) — it
      // returns in seconds and never hits the proxy/keep-alive timeout that made the old
      // LLM-personalized build "return nothing". A 90s ceiling turns a backend stall into a
      // retryable error instead of an endless spinner.
      const deliv = await api.deliverables(
        { ...briefFromForm(), use_llm: false, draft_limit: 10 },
        { signal: AbortSignal.timeout(90_000) },
      );
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
      setDelivError(err instanceof Error ? err.message : String(err));
    } finally {
      setDelivLoading(false);
    }
  }

  // #7 — optional, explicit upgrade: have the LLM write the downloadable page drafts. Runs
  // as a background job (returns a job id immediately, then we poll), so the slow build
  // never holds a request open. The instant in-app plan stays exactly as it is; only the
  // downloadable assets change. On failure the ready-made deterministic files remain.
  async function personalizeFiles() {
    setPersonalizing(true);
    setPersonalizeError(null);
    try {
      const { job_id } = await api.startPersonalize({ ...briefFromForm(), use_llm: true, draft_limit: 10 });
      personalizeJobIdRef.current = job_id;
      let job = await api.personalizeStatus(job_id);
      setPersonalizeJob(job);
      let tries = 0;
      while ((job.status === "queued" || job.status === "running") && tries < 450) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        job = await api.personalizeStatus(job_id);
        setPersonalizeJob(job);
        tries += 1;
      }
      if (job.status === "succeeded" && job.result) {
        setDeliverables(job.result);
      } else {
        setPersonalizeError(
          job.error ||
            "We couldn't personalize your files just now — your ready-made files are still available below.",
        );
      }
    } catch (err) {
      setPersonalizeError(err instanceof Error ? err.message : String(err));
    } finally {
      setPersonalizing(false);
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

  // v5 CH-01: the URL is the only input that can ever block — a blank name derives from
  // the domain (briefFromForm here, BriefRequest server-side), so step 1 never gates.
  const nextBlocker: string | null = (() => {
    if (step === 0 && hasSite && !domain.trim()) return "Add your website address, or pick “I don't have a site yet”";
    return null;
  })();

  const isLast = step === STEPS.length - 1;

  // #3 — recommendations + any free-typed custom goals (goals not in the preset list).
  const recommended = recommendedGoals(profile, !!location.trim());
  const customGoals = goals.filter((g) => !GOAL_OPTIONS.some((o) => o.label === g));

  // ── intake section bodies — shared verbatim between the four-step wizard and the
  // one-page review (?review=1), so the two layouts can never drift apart ─────────────

  const siteFields = (
    <div className="space-y-6">
      <div>
        <span className="field-label">Do you have a website?</span>
        <div className="inline-flex rounded-xl border border-white/[0.13] bg-white/[0.04] p-1">
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
                hasSite === v ? "bg-white/10 font-medium text-ink" : "text-ink-300 hover:text-ink-700"
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
        {/* NO autoFocus here: React focuses on hydration, and the browser scrolls
            a focused element into view — the page would open scrolled to this
            input instead of at the top. */}
        <input
          className="input"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="yourbusiness.com"
          inputMode="url"
          aria-label="Website address"
        />
      </Field>

      {/* One-pager only: the domain was edited away from the site we scanned — offer a
          re-scan that refills every section (the wizard gets this via "Continue"). */}
      {onePage && hasSite && domain.trim() !== "" && lastProfiledDomainRef.current !== domain.trim() && !prefilling && (
        <LiquidButton
          variant="secondary"
          className="px-5 py-2.5"
          onClick={() => void startReview(domain.trim())}
        >
          Scan this address & refill the page
        </LiquidButton>
      )}

      <p className="text-[13px] leading-[1.55] text-[#6f6f77]">
        {hasSite
          ? "We take a quick look and show your AI visibility score in seconds — then pre-fill the next steps for you."
          : "No website yet? No problem — we'll plan your ideal one from scratch."}
      </p>

      {/* The seconds-long prefill crawl: a per-section progress card so the
          wait reads as motion toward a filled-in "About you", not a spinner. */}
      {prefilling && hasSite && <PrefillProgress done={prefillDone} />}
    </div>
  );

  // Score ring + crawl outcome notes + the re-crawl opt-in. The wizard shows these at the
  // top of "About you"; the one-pager lifts them into its page-level summary.
  const aboutStatus = (
    <>
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
        <label className="step-in flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 py-2.5 text-sm">
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
    </>
  );

  const aboutFields = (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-[18px]">
      {/* v5 CH-01: NOT required — a blank name derives from the domain (briefFromForm here,
          BriefRequest server-side), and nothing but the URL may block submission. The
          asterisk used to claim otherwise. */}
      <Field label="Business name" hint="optional — we'll use your domain">
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
  );

  const competitorFields = (
    <CompetitorPicker
      businessName={name}
      category={category}
      location={location}
      domain={domain}
      services={splitList(servicesText)}
      selected={competitors}
      onChange={setCompetitors}
    />
  );

  const goalsFields = (
    <>
      {/* #3 — the analysis pre-selects the goals it recommends; the user
          unticks what doesn't fit or adds their own below. */}
      <div className="step-in rounded-xl border border-white/10 bg-white/[0.03] px-4 py-[13px] text-[13.5px] leading-[1.55] text-ink-500">
        <strong className="font-semibold text-ink">
          {profile ? "We pre-selected goals from your analysis." : "Pick what success looks like."}
        </strong>{" "}
        Keep what fits, untick what doesn&apos;t, or add your own.
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,270px),1fr))] gap-[13px]">
        {GOAL_OPTIONS.map((g, i) => {
          const on = goals.includes(g.label);
          const rec = recommended.has(g.label);
          return (
            <button
              key={g.label}
              type="button"
              aria-pressed={on}
              onClick={() => toggleGoal(g.label)}
              className={`step-in flex flex-col gap-[7px] rounded-[14px] border p-[17px] pb-[15px] text-left transition-[transform,border-color,background-color] duration-[250ms] ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:border-white/40 ${
                on ? "border-white/35 bg-white/[0.055]" : "border-white/10 bg-white/[0.018]"
              }`}
              style={{ animationDelay: `${i * 50}ms` }}
            >
              <span className="flex flex-wrap items-center gap-x-[11px] gap-y-1.5">
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all duration-200 ${
                    on ? "border-accent bg-accent text-paper" : "border-white/30 bg-transparent"
                  }`}
                >
                  {on && <Check className="animate-pop" width={12} height={12} />}
                </span>
                <span className="text-[15px] font-semibold text-ink">{g.label}</span>
                {rec && (
                  <span className="whitespace-nowrap rounded-full border border-white/15 px-2 py-[3px] font-mono text-[9px] font-medium uppercase tracking-[0.07em] text-ink-500">
                    {profile ? "Rec. by AI" : "Suggested"}
                  </span>
                )}
              </span>
              <span className="pl-[31px] text-[13px] leading-[1.5] text-ink-300">{g.hint}</span>
            </button>
          );
        })}
      </div>

      {/* #3 — custom goals: any objective the presets don't cover. */}
      <div>
        {customGoals.length > 0 && (
          <div className="mb-2.5 flex flex-wrap gap-2">
            {customGoals.map((cg) => (
              <span key={cg} className="chip">
                {cg}
                <button
                  type="button"
                  onClick={() => toggleGoal(cg)}
                  aria-label={`Remove goal ${cg}`}
                  className="ml-0.5 flex h-4 w-4 items-center justify-center rounded-full text-ink-300 transition-colors hover:bg-ink/10 hover:text-ink"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2.5">
          <input
            className="input min-w-0 flex-1"
            value={customGoalInput}
            onChange={(e) => setCustomGoalInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustomGoal();
              }
            }}
            placeholder="Add your own — e.g. Rank for niche topics"
            aria-label="Add a custom goal"
          />
          <LiquidButton
            variant="secondary"
            className="shrink-0 px-6"
            onClick={addCustomGoal}
            disabled={!customGoalInput.trim()}
          >
            + Add
          </LiquidButton>
        </div>
      </div>

      <Field label="Anything frustrating you right now?" hint="optional">
        <textarea
          className="input min-h-20 resize-y"
          value={challenges}
          onChange={(e) => setChallenges(e.target.value)}
          placeholder="e.g. Customers tell us ChatGPT never mentions our shop…"
          aria-label="Anything frustrating you right now"
        />
      </Field>

      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3.5 text-sm">
        <input
          type="checkbox"
          className="toggle"
          checked={useLlm}
          onChange={(e) => setUseLlm(e.target.checked)}
        />
        <span>
          <span className="block font-medium text-ink">Write my downloadable files with AI</span>
          <span className="block text-xs text-ink-300">
            Optional. Your interactive plan is ready instantly either way — turn this on and we&apos;ll
            offer to AI-write every downloadable page for you on the results screen (a few minutes,
            only when you ask).
          </span>
        </span>
      </label>
    </>
  );

  // The expectation-setting line beside the "Build my plan" CTA (wizard step 4 footer /
  // one-pager build panel).
  const buildExpectation = !loading && (
    <p className="text-[13px] leading-[1.55] text-[#6f6f77]">
      {noSite
        ? "“Build my plan” usually takes under a minute."
        : "You already have your score — “Build my plan” runs the full page-by-page review, usually around 10 minutes. You'll see progress as it goes, and you can leave this tab open."}
    </p>
  );

  return (
    <section className="relative" style={{ padding: "clamp(70px, 9vh, 110px) 0" }}>
      {/* blueprint grid backdrop, masked to an ellipse so it fades out (design §3) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px)",
          backgroundSize: "32px 32px",
          backgroundPosition: "center",
          WebkitMaskImage: "radial-gradient(ellipse 60% 55% at 50% 40%, #000 20%, transparent 100%)",
          maskImage: "radial-gradient(ellipse 60% 55% at 50% 40%, #000 20%, transparent 100%)",
        }}
      />
      <div className="relative mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
      <div>
        <Reveal>
          <SheetTag no="03">Your plan builder</SheetTag>
        </Reveal>
        {view === "results" ? (
          <h2
            className="mb-10 mt-[26px] font-semibold"
            style={{ fontSize: "clamp(2.1rem, 4.8vw, 3.8rem)", lineHeight: 1.08, letterSpacing: "-0.035em" }}
          >
            Your results
          </h2>
        ) : onePage ? (
          <>
            {/* key: swapping headline copy in place must REMOUNT DisplayH2 — its lead words
                are keyed RisingWords inside a once-only whileInView container, so replaced
                words would mount into an already-finished animation and stay hidden. */}
            <DisplayH2 key="onepage-h" lead="Everything we found, on one" accent="page" trail="." className="mb-[18px] mt-[26px]" />
            <p
              className="mb-[clamp(40px,6vh,60px)] max-w-[58ch] text-ink-500"
              style={{ fontSize: "clamp(1rem, 1.6vw, 1.15rem)", lineHeight: 1.6 }}
            >
              Your website, your business, your competitors, and your goals — prefilled from the scan
              and laid out below. Edit anything, then run the full audit.
            </p>
          </>
        ) : (
          <>
            <DisplayH2 key="wizard-h" lead="Start with your website. We’ll do the" accent="rest" trail="." className="mb-[18px] mt-[26px]" />
            <p
              className="mb-[clamp(40px,6vh,60px)] max-w-[54ch] text-ink-500"
              style={{ fontSize: "clamp(1rem, 1.6vw, 1.15rem)", lineHeight: 1.6 }}
            >
              Enter your address and we&apos;ll figure out your industry, your gaps, and a step-by-step plan
              to make your business the one AI recommends.
            </p>
          </>
        )}
      </div>

      {view === "wizard" && resume && !prefilling && (
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
          {profile && (
            <div className="mb-4 animate-fade-up">
              <GamificationStrip
                domain={hasSite ? domain.trim() || undefined : undefined}
                aeoScore={aeoScore(profile)}
              />
            </div>
          )}
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
          {/* v5 CH-02b — the checkout return. Rendered ABOVE the packs so a buyer coming
              back from Stripe sees an acknowledgement before anything else, instead of the
              studio's ordinary empty state. Every branch says something true. */}
          {checkout && (
            <div
              role="status"
              aria-live="polite"
              className={`mb-6 rounded-xl border p-4 ${
                checkout.state === "cancelled"
                  ? "border-white/[0.13] bg-white/[0.03]"
                  : "border-accent/30 bg-accent/[0.06]"
              }`}
            >
              {checkout.state === "confirming" && (
                <p className="text-[13.5px] leading-[1.6] text-ink">
                  Payment received — unlocking
                  {checkout.packIndex ? ` Pack ${checkout.packIndex}` : " your pack"}…
                </p>
              )}
              {checkout.state === "unlocked" && (
                <p className="text-[13.5px] leading-[1.6] text-ink">
                  {checkout.packIndex ? `Pack ${checkout.packIndex} is` : "Your pack is"} unlocked
                  — thank you. It&apos;s open below.
                </p>
              )}
              {checkout.state === "pending_grant" && (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="max-w-[60ch] text-[13.5px] leading-[1.6] text-ink">
                    Your payment went through, but the unlock hasn&apos;t come back from our
                    payment provider yet. This usually takes a few seconds. Nothing is lost —
                    your pack will appear here.
                  </p>
                  <button
                    type="button"
                    onClick={() => void recheckCheckout(checkout.packIndex)}
                    className="btn-ghost shrink-0 text-[13px]"
                  >
                    Check again
                  </button>
                </div>
              )}
              {checkout.state === "unknown_run" && (
                <p className="max-w-[64ch] text-[13.5px] leading-[1.6] text-ink">
                  Payment received — thank you. We couldn&apos;t tell which audit to reopen
                  from this browser, so run or reopen your site below and the pack will be
                  unlocked on it.
                </p>
              )}
              {checkout.state === "cancelled" && (
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[13.5px] leading-[1.6] text-ink-300">
                    Checkout cancelled — you haven&apos;t been charged.
                  </p>
                  <button
                    type="button"
                    onClick={() => setCheckout(null)}
                    aria-label="Dismiss"
                    className="shrink-0 text-ink-300 hover:text-ink"
                  >
                    ✕
                  </button>
                </div>
              )}
            </div>
          )}
          {packs.length > 0 && (
            <section className="mb-10" aria-labelledby="studio-packs-h">
              <h3 id="studio-packs-h" className="mb-1 text-[18px] font-semibold tracking-[-0.01em] text-ink">
                Your work, grouped into packs
              </h3>
              <p className="mb-4 max-w-[64ch] text-[13.5px] leading-[1.6] text-ink-300">
                Ordered by expected impact — your homepage pack comes first.
              </p>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-4">
                {packs.map((pack) => (
                  <PackCard
                    key={pack.pack_index}
                    pack={pack}
                    ctaMode={authEnabled ? "gated" : "preview"}
                    onUnlock={() => handleUnlock(pack.pack_index)}
                    onOpen={pack.locked ? undefined : () => setOpenPack((cur) => (cur === pack.pack_index ? null : pack.pack_index))}
                    opened={openPack === pack.pack_index}
                  />
                ))}
              </div>
              {/* The standalone TicketBoard that used to sit here is GONE (Phase 3 item
                  3.4). It was a second to-do surface with its own layout and its own
                  progress, sitting under the plan the user was already working. Those same
                  fixes now render inside "Your plan", bucketed into Quick Wins / Foundation
                  / Growth & Scale — opening a pack here selects it there. Only the duplicate
                  UI was removed: /api/tickets/{run}/{pack} is unchanged and is what the plan
                  section reads. */}
              {runId != null && openPack != null && packs.some((p) => p.pack_index === openPack && !p.locked) && (
                <p className="mt-4 text-[13px] leading-[1.6] text-ink-300">
                  This pack&apos;s fixes are in <span className="text-ink">Your plan</span>, and
                  its page-by-page scores are under <span className="text-ink">Pages</span> —
                  both below.
                </p>
              )}
            </section>
          )}
          {unlockOpen && domain.trim() && (
            <UnlockModal
              domain={domain.trim()}
              packIndex={unlockPack ?? undefined}
              runId={runId ?? undefined}
              onUnlocked={() => {
                setUnlockOpen(false);
                void refreshPacks();
              }}
              onClose={() => setUnlockOpen(false)}
            />
          )}
          <ResultsView
            // Derive the display name the same way briefFromForm / the server do, so an
            // empty name never collapses distinct plans onto one shared localStorage key.
            businessName={name.trim() || deriveName(domain) || "My business"}
            domain={hasSite ? domain.trim() || undefined : undefined}
            profile={profile}
            plan={plan}
            auditJob={auditJob}
            deliverables={deliverables}
            delivLoading={delivLoading}
            delivError={delivError}
            aiPersonalization={useLlm}
            cmsType={profileResult?.cms_type ?? null}
            // item 3.4 — the pack grid lives here, but its fixes render inside
            // "Your plan". Opening a pack above selects it there, and vice versa, so
            // the two surfaces always agree on which pack is being worked.
            packContext={
              runId != null && packs.length > 0
                ? {
                    runId,
                    packs,
                    selectedPack: openPack,
                    onSelectPack: setOpenPack,
                    onUnlock: handleUnlock,
                  }
                : undefined
            }
            onGenerateDeliverables={generateDeliverables}
            onPersonalize={personalizeFiles}
            personalizing={personalizing}
            personalizeError={personalizeError}
            personalizeProgress={personalizeJob?.progress ?? null}
            onDownloadZip={downloadZip}
            onEdit={() => setView("wizard")}
          />
        </>
      ) : prefilling ? (
        <AnalysisSequence domain={domain.trim() || "your site"} />
      ) : onePage ? (
        /* The one-page review: the same four intake sections the wizard steps through,
           stacked and prefilled — score summary up top, one "Build my plan" CTA at the
           end. The rail on the left jumps to sections instead of switching steps. */
        <div className="grid animate-fade-up-slow items-start gap-[22px] md:grid-cols-[minmax(230px,290px)_minmax(0,1fr)]">
          <SectionRail />
          <div className="min-w-0">
            {error && <ErrorNote message={error} />}
            {(profileResult || (noSite && hasSite)) && (
              <div className="mb-[22px] space-y-4">{aboutStatus}</div>
            )}
            <div className="flex flex-col gap-[22px]">
              <ReviewSection index={0}>{siteFields}</ReviewSection>
              <ReviewSection index={1}>{aboutFields}</ReviewSection>
              <ReviewSection index={2}>{competitorFields}</ReviewSection>
              <ReviewSection index={3}>
                <div className="space-y-6">{goalsFields}</div>
              </ReviewSection>

              {/* the build panel — the page's single CTA */}
              <div
                className="rounded-[20px] border border-white/10"
                style={{
                  background: "linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01))",
                  padding: "clamp(24px, 3vw, 36px)",
                  boxShadow: "0 24px 70px -30px rgba(0,0,0,0.8)",
                }}
              >
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="max-w-[54ch] space-y-2">
                    <h3 className="text-[17px] font-semibold text-ink">
                      {noSite
                        ? "Happy with the details? Build your plan."
                        : "Happy with the details? Run the full audit."}
                    </h3>
                    {buildExpectation}
                  </div>
                  <div className="text-right">
                    <LiquidButton
                      variant="primary"
                      className="px-[26px] py-[13px]"
                      onClick={createPlan}
                      disabled={loading || (hasSite && !domain.trim())}
                    >
                      {loading ? (
                        <>
                          <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                          {noSite ? "Building your plan…" : "Reviewing your website…"}
                        </>
                      ) : hasRunResults ? (
                        "Rebuild my plan →"
                      ) : (
                        "Build my plan →"
                      )}
                    </LiquidButton>
                    {hasSite && !domain.trim() && (
                      <p className="mt-1.5 text-xs text-ink-300">Add your website address in section 01 first</p>
                    )}
                  </div>
                </div>
                {loading && !noSite && auditJob && (
                  <div className="mt-5">
                    <AnalysisProgress job={auditJob} onCancel={cancelAudit} />
                  </div>
                )}
                {hasRunResults && !loading && (
                  <button
                    onClick={() => setView("results")}
                    className="mt-4 text-[13px] font-medium text-ink-500 underline-offset-4 transition-colors hover:text-ink hover:underline"
                  >
                    View my results →
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="grid animate-fade-up-slow items-start gap-[22px] md:grid-cols-[minmax(230px,290px)_minmax(0,1fr)]">
          <Stepper current={step} onJump={setStep} />

          <div>
            {error && <ErrorNote message={error} />}

            {/* the panel — gradient fill, 20px radius, deep soft shadow (design §3) */}
            <div
              className="rounded-[20px] border border-white/10"
              style={{
                background: "linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01))",
                padding: "clamp(26px, 3.4vw, 42px)",
                boxShadow: "0 24px 70px -30px rgba(0,0,0,0.8)",
              }}
            >
              {/* keyed so header + content rise together on every step change */}
              <div key={step} className="panel-in">
                <StepHeader index={step} />
                {step === 0 && siteFields}

                {step === 1 && (
                  <div className="space-y-5">
                    {aboutStatus}
                    {aboutFields}
                  </div>
                )}

                {step === 2 && competitorFields}

                {step === 3 && (
                  <div className="space-y-6">
                    {goalsFields}
                    {/* The actual CTA lives in the panel footer ("Build my plan →") — this
                        is the expectation-setting line beside it. */}
                    {buildExpectation}
                    {loading && !noSite && auditJob && <AnalysisProgress job={auditJob} onCancel={cancelAudit} />}
                    {analyzed && !loading && (
                      <button
                        onClick={() => setView("results")}
                        className="text-[13px] font-medium text-ink-500 underline-offset-4 transition-colors hover:text-ink hover:underline"
                      >
                        View my results →
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* panel footer nav — glass pills, inside the panel (design §3). The last
                  step's primary reads "Build my plan →" and fires the analysis. */}
              <div className="mt-[30px] flex flex-wrap items-center justify-between gap-3.5 border-t border-white/[0.08] pt-6">
                <LiquidButton
                  variant="secondary"
                  className="px-6 py-3"
                  disabled={step === 0}
                  onClick={() => {
                    // Returning to the website-entry step drops site A's crawl-derived
                    // prefills so a newly-entered site starts clean (the domain stays).
                    if (step === 1) resetPrefilled();
                    setStep((s) => Math.max(0, s - 1));
                  }}
                >
                  ← Back
                </LiquidButton>
                <div className="flex flex-wrap items-center justify-end gap-4">
                  {/* After the URL+crawl, every later step is prefilled/optional — let the
                      user bail straight to the comprehensive analysis (#1: URL is enough). */}
                  {step >= 1 && !isLast && (
                    <button
                      onClick={() => {
                        setStep(STEPS.length - 1); // land on the step that shows live progress
                        createPlan();
                      }}
                      disabled={loading || prefilling}
                      className="text-[13px] text-ink-300 underline-offset-4 transition-colors hover:text-ink hover:underline disabled:opacity-40"
                      title="Skip the rest — analyze your site with what we already have"
                    >
                      Skip — just analyze my site
                    </button>
                  )}
                  <div className="text-right">
                    {isLast ? (
                      <LiquidButton
                        variant="primary"
                        className="px-[26px] py-[13px]"
                        onClick={createPlan}
                        disabled={loading}
                      >
                        {loading ? (
                          <>
                            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                            {noSite ? "Building your plan…" : "Reviewing your website…"}
                          </>
                        ) : analyzed ? (
                          "Rebuild my plan →"
                        ) : (
                          "Build my plan →"
                        )}
                      </LiquidButton>
                    ) : (
                      <LiquidButton
                        variant="primary"
                        className="px-[26px] py-[13px]"
                        onClick={() => (step === 0 ? handleWebsiteNext() : advance(step))}
                        disabled={nextBlocker !== null || prefilling}
                      >
                        {prefilling ? (
                          <>
                            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                            Taking a look…
                          </>
                        ) : (
                          "Continue →"
                        )}
                      </LiquidButton>
                    )}
                    {nextBlocker && <p className="mt-1.5 text-xs text-ink-300">{nextBlocker}</p>}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
      </div>
    </section>
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

// #2 — the centered analysis experience that replaces the wizard while the fast crawl runs.
// The crawl is a single call that returns in seconds; the stages animate the work so the
// wait reads as the AI actively examining the site, never a silent spinner. The sequence
// unmounts the instant the crawl resolves (prefilling → false) and step 1 lands with the
// provisional score, so the motion flows straight into a result.
function AnalysisSequence({ domain }: { domain: string }) {
  const reduced = useReducedMotion();
  const [active, setActive] = useState(reduced ? ANALYSIS_STAGES.length - 1 : 0);
  useEffect(() => {
    if (reduced) return;
    const id = setInterval(
      () => setActive((i) => Math.min(i + 1, ANALYSIS_STAGES.length - 1)),
      650,
    );
    return () => clearInterval(id);
  }, [reduced]);
  const pct = Math.round(((active + 1) / ANALYSIS_STAGES.length) * 100);

  return (
    <div className="step-in mx-auto max-w-xl">
      <div className="card relative overflow-hidden p-7 sm:p-9">
        <div className="blueprint-grid blueprint-grid-fade pointer-events-none absolute inset-0 opacity-60" aria-hidden />
        <div className="relative">
          <div className="flex items-center gap-2.5 text-accent">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-accent" />
            </span>
            <span className="label-mono !text-accent">Analyzing</span>
          </div>
          <h3 className="mt-2 text-xl font-semibold">
            Looking at <span className="font-mono text-ink">{domain}</span>…
          </h3>
          <p className="mt-1 text-sm text-ink-500">
            We&apos;re reading your site the way an AI assistant would. This only takes a few seconds.
          </p>

          <div
            className="mt-5 h-1.5 overflow-hidden rounded-full bg-ink/[0.07]"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-busy
            aria-label="Analysis progress"
          >
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent to-accent-600 transition-[width] duration-500 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>

          <ol className="mt-5 space-y-2.5">
            {ANALYSIS_STAGES.map((label, i) => {
              const done = i < active;
              const current = i === active;
              return (
                <li
                  key={label}
                  className={`flex items-center gap-3 text-sm transition-opacity duration-300 ${
                    i <= active ? "opacity-100" : "opacity-40"
                  }`}
                >
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full transition-colors duration-300 ${
                      done
                        ? "bg-emerald-500 text-white"
                        : current
                          ? "border-2 border-accent/60"
                          : "border border-ink/15"
                    }`}
                  >
                    {done ? (
                      <Check className="animate-pop" width={11} height={11} />
                    ) : current ? (
                      <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-accent/40 border-t-accent" />
                    ) : null}
                  </span>
                  <span className={done ? "text-ink-500" : current ? "font-medium text-ink" : "text-ink-300"}>
                    {label}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </div>
  );
}

// The stepper rail (design §3): 28px numbered circles — done rows get a solid white
// circle with a dark check, the active row a soft fill + brighter border. Every row
// stays clickable (jump to any step). Collapses to a horizontal strip below md.
function Stepper({ current, onJump }: { current: number; onJump: (i: number) => void }) {
  return (
    <nav aria-label="Steps" className="md:sticky md:top-24 md:self-start">
      <ol className="flex gap-1.5 overflow-x-auto pb-2 md:flex-col md:overflow-visible md:pb-0">
        {STEPS.map(({ label }, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li key={label} className="shrink-0 md:shrink">
              <button
                onClick={() => onJump(i)}
                aria-current={active ? "step" : undefined}
                className={`flex w-full items-center gap-3.5 rounded-xl border px-4 py-[13px] text-left transition-colors duration-[250ms] ${
                  active ? "border-white/[0.18] bg-white/[0.07]" : "border-transparent hover:bg-white/[0.05]"
                }`}
              >
                <span
                  aria-hidden
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-[10.5px] font-medium transition-all duration-[250ms] ${
                    done
                      ? "border-ink bg-ink text-paper"
                      : active
                        ? "border-white/40 bg-white/10 text-accent"
                        : "border-white/[0.14] bg-white/[0.03] text-ink-300"
                  }`}
                >
                  {done ? <Check className="animate-pop" width={12} height={12} /> : String(i + 1).padStart(2, "0")}
                </span>
                <span
                  className={`whitespace-nowrap text-[14.5px] font-medium transition-colors duration-[250ms] md:whitespace-normal ${
                    active ? "text-accent" : done ? "text-ink-700" : "text-ink-300"
                  }`}
                >
                  {label}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

// The one-page review's left rail — same look as the wizard Stepper, but every section is
// already on the page, so rows are smooth-scroll anchor jumps instead of step switches.
function SectionRail() {
  return (
    <nav aria-label="Sections" className="md:sticky md:top-24 md:self-start">
      <ol className="flex gap-1.5 overflow-x-auto pb-2 md:flex-col md:overflow-visible md:pb-0">
        {STEPS.map(({ label }, i) => (
          <li key={label} className="shrink-0 md:shrink">
            <a
              href={`#review-sec-${i}`}
              onClick={(e) => {
                e.preventDefault();
                document.getElementById(`review-sec-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
              className="flex w-full items-center gap-3.5 rounded-xl border border-transparent px-4 py-[13px] text-left transition-colors duration-[250ms] hover:bg-white/[0.05]"
            >
              <span
                aria-hidden
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/[0.14] bg-white/[0.03] font-mono text-[10.5px] font-medium text-ink-300"
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="whitespace-nowrap text-[14.5px] font-medium text-ink-500 md:whitespace-normal">
                {label}
              </span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

// One stacked panel of the one-page review — the same card treatment as the wizard panel,
// headed by the section's wizard label + blurb so the two layouts read as the same product.
function ReviewSection({ index, children }: { index: number; children: React.ReactNode }) {
  return (
    <section
      id={`review-sec-${index}`}
      aria-labelledby={`review-sec-h-${index}`}
      className="rounded-[20px] border border-white/10"
      style={{
        background: "linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01))",
        padding: "clamp(24px, 3vw, 36px)",
        boxShadow: "0 24px 70px -30px rgba(0,0,0,0.8)",
        // Anchor jumps must clear the fixed top bar.
        scrollMarginTop: 96,
      }}
    >
      <div className="mb-[22px] flex items-start gap-3.5">
        <span
          aria-hidden
          className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/[0.18] bg-white/[0.06] font-mono text-[10.5px] font-medium text-accent"
        >
          {String(index + 1).padStart(2, "0")}
        </span>
        <div>
          <h3 id={`review-sec-h-${index}`} className="text-xl font-semibold tracking-[-0.02em] text-accent">
            {STEPS[index].label}
          </h3>
          <p className="mt-1.5 text-[14px] leading-[1.6] text-ink-500">{STEPS[index].blurb}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

function StepHeader({ index }: { index: number }) {
  return (
    <div className="mb-[30px]">
      <span className="font-mono text-[10.5px] font-medium uppercase tracking-[0.3em] text-ink-300">
        Step {String(index + 1).padStart(2, "0")} / {String(STEPS.length).padStart(2, "0")}
      </span>
      <h3 className="mt-3.5 text-2xl font-semibold tracking-[-0.02em] text-accent">{STEPS[index].label}</h3>
      <p className="mt-2 text-[15px] leading-[1.6] text-ink-500">{STEPS[index].blurb}</p>
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
        {hint && <span className="ml-1.5 normal-case tracking-normal text-[#5c5c64]">({hint})</span>}
      </span>
      {children}
    </div>
  );
}
