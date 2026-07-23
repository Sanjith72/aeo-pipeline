"use client";

// The v5 free overview (CH-09) — the no-signup deliverable behind the hero's URL field.
// Renders POST /api/overview: five homepage skill scores, an impact-ordered pack preview
// (Pack 1 open, deeper packs locked), what's missing, and on-site competitor names, with
// one CTA into the full audit (/studio autobuild). Design system per CH-12: existing
// tokens only (.card/.btn/.input, ink/accent, label-mono); scores are length-encoded
// monochrome meters — no new palette, identity carried by text labels.

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { OverviewResponse, SkillKey, SkillScore } from "@/lib/types";
import { SheetTag } from "@/components/chrome";
import { Reveal } from "@/components/motion/primitives";
import { ArrowRight } from "@/components/ui/icons";

const SKILL_META: { key: SkillKey; label: string; blurb: string }[] = [
  { key: "messaging", label: "Messaging", blurb: "Is it clear what you do, for whom, and why it matters?" },
  { key: "conversion", label: "Conversion", blurb: "Is there an obvious next step for a ready buyer?" },
  { key: "discovery_visibility", label: "Discovery & Visibility", blurb: "Can people — and AI — find and read this page?" },
  { key: "proof_trust", label: "Proof & Trust", blurb: "Do you show evidence a stranger would believe?" },
  { key: "structure_ux", label: "Structure & UX", blurb: "Does the page read in clean, scannable chunks?" },
];

// Honest provenance labels for SkillScore.confidence — "provisional" is the P1
// heuristic pass; the AI-judged Messaging/Conversion arrive with the deep audit work.
const CONFIDENCE_LABEL: Record<string, string> = {
  deterministic: "measured",
  hybrid: "AI-refined",
  provisional: "provisional",
  neutral: "not yet judged",
};

// The overview is one ~15s call; these stages animate the wait as active examination
// (same pattern as the wizard's ANALYSIS_STAGES) instead of a silent spinner.
const LOADING_STAGES = [
  "Reading your homepage",
  "Discovering your pages",
  "Scoring the five skills",
  "Grouping pages into packs",
  "Checking what's missing",
] as const;

function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname === "/" ? u.hostname : u.pathname;
  } catch {
    return url;
  }
}

/** Length-encoded 0-100 meter in house monochrome (track white/10, fill accent). */
function Meter({ value }: { value: number }) {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
      role="img"
      aria-label={`Score ${value} out of 100`}
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function SkillCard({ label, blurb, skill }: { label: string; blurb: string; skill: SkillScore }) {
  return (
    <div className="card flex h-full flex-col gap-3 p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{label}</h3>
        <span className="font-display text-[26px] font-semibold leading-none text-accent">{skill.score}</span>
      </div>
      <Meter value={skill.score} />
      <p className="m-0 text-[13px] leading-[1.55] text-ink-300">{blurb}</p>
      {skill.suggestions.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {skill.suggestions.slice(0, 2).map((s) => (
            <li key={s.id} className="flex gap-2 text-[13.5px] leading-[1.55] text-ink-500">
              <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-white/40" />
              {s.text}
            </li>
          ))}
        </ul>
      )}
      <span className="label-mono mt-auto !text-[10px] text-ink-300">
        {CONFIDENCE_LABEL[skill.confidence] ?? skill.confidence}
      </span>
    </div>
  );
}

export function OverviewView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const domain = (searchParams.get("domain") ?? "").trim();

  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState(0);
  const [input, setInput] = useState("");
  const loadedDomainRef = useRef<string | null>(null);

  useEffect(() => {
    if (!domain || loadedDomainRef.current === domain) return;
    loadedDomainRef.current = domain;
    setLoading(true);
    setError(null);
    setData(null);
    api.track("overview_requested", { domain });
    api
      .overview({ domain })
      .then(setData)
      .catch((err) => {
        // Allow a retry of the same domain after a failure.
        loadedDomainRef.current = null;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [domain]);

  // Rotate the examination stages while the call is in flight.
  useEffect(() => {
    if (!loading) return;
    setStage(0);
    const t = setInterval(() => setStage((s) => (s + 1) % LOADING_STAGES.length), 2400);
    return () => clearInterval(t);
  }, [loading]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const v = input.trim();
    if (!v) return;
    router.replace(`/overview?domain=${encodeURIComponent(v)}`);
  }

  return (
    <section className="relative" style={{ padding: "clamp(70px, 9vh, 110px) 0 clamp(90px, 12vh, 140px)" }}>
      <div className="relative mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
        <Reveal>
          <SheetTag no="01">Your free overview</SheetTag>
        </Reveal>

        {!domain && (
          <div className="mt-[26px] max-w-[560px]">
            <h1
              className="font-semibold text-ink"
              style={{ fontSize: "clamp(2rem, 4vw, 3rem)", lineHeight: 1.1, letterSpacing: "-0.03em" }}
            >
              Paste your website. See how it <em className="word-accent">really</em> reads.
            </h1>
            <form onSubmit={submit} className="mt-7 flex flex-col gap-3 sm:flex-row">
              <input
                type="text"
                inputMode="url"
                autoComplete="url"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="yourwebsite.com"
                aria-label="Your website address"
                className="input flex-1"
              />
              <button type="submit" className="btn-primary shrink-0">
                Analyze my site <ArrowRight width={13} height={13} />
              </button>
            </form>
            <p className="mt-3 text-[13px] text-ink-300">Free. No signup — results in about a minute.</p>
          </div>
        )}

        {domain && loading && (
          <div className="mt-[26px] max-w-[560px]" aria-live="polite">
            <h1 className="font-semibold text-ink" style={{ fontSize: "clamp(1.6rem, 3vw, 2.4rem)", lineHeight: 1.15 }}>
              Reading <span className="text-accent">{domain}</span>…
            </h1>
            <ul className="m-0 mt-6 flex list-none flex-col gap-2.5 p-0">
              {LOADING_STAGES.map((label, i) => (
                <li
                  key={label}
                  className={`flex items-center gap-3 text-[14.5px] transition-opacity duration-300 ${
                    i === stage ? "text-ink" : i < stage ? "text-ink-500" : "text-ink-300 opacity-60"
                  }`}
                >
                  <span
                    aria-hidden
                    className={`h-1.5 w-1.5 rounded-full ${i === stage ? "animate-pulse bg-accent" : i < stage ? "bg-white/50" : "bg-white/20"}`}
                  />
                  {label}
                </li>
              ))}
            </ul>
          </div>
        )}

        {domain && error && !loading && (
          <div className="card mt-[26px] max-w-[620px] p-6">
            <h1 className="text-[20px] font-semibold text-ink">We couldn&apos;t finish that look</h1>
            <p className="mt-2 text-[14px] leading-[1.6] text-ink-500">{error}</p>
            <div className="mt-5 flex flex-wrap gap-3">
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  loadedDomainRef.current = null;
                  setError(null);
                  router.replace(`/overview?domain=${encodeURIComponent(domain)}`);
                  // Same param → the effect won't re-fire from navigation alone; nudge it.
                  setLoading(true);
                  api
                    .overview({ domain })
                    .then((d) => {
                      loadedDomainRef.current = domain;
                      setData(d);
                    })
                    .catch((err) => setError(err instanceof Error ? err.message : String(err)))
                    .finally(() => setLoading(false));
                }}
              >
                Try again
              </button>
              <Link href="/" className="btn-ghost">
                Start over
              </Link>
            </div>
          </div>
        )}

        {data && !loading && (
          <div className="mt-[26px] flex flex-col gap-10">
            <header className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h1
                  className="font-semibold text-ink"
                  style={{ fontSize: "clamp(1.8rem, 3.6vw, 2.8rem)", lineHeight: 1.1, letterSpacing: "-0.03em" }}
                >
                  {data.domain}
                </h1>
                <p className="mt-2 max-w-[64ch] text-[14.5px] leading-[1.6] text-ink-500">
                  {data.site.industry ? `${data.site.industry} · ` : ""}
                  {data.site.discovered} pages found
                  {data.site.location ? ` · ${data.site.location}` : ""}
                  {data.cached ? " · from earlier today" : ""}
                </p>
              </div>
              {data.skills && (
                <div className="card flex items-center gap-4 px-5 py-4">
                  <span className="label-mono !text-[10px] text-ink-300">Overall</span>
                  <span className="font-display text-[40px] font-semibold leading-none text-accent">
                    {data.skills.overall}
                  </span>
                  <span className="text-[12px] text-ink-300">/ 100</span>
                </div>
              )}
            </header>

            {data.route === "dead" && (
              <div className="card max-w-[640px] p-6">
                <h2 className="text-[18px] font-semibold text-ink">We couldn&apos;t read this site</h2>
                <p className="mt-2 text-[14px] leading-[1.6] text-ink-500">
                  The crawl found nothing usable — the site may be down, brand new, or blocking
                  robots. You can still build a plan from a short business brief.
                </p>
                <Link href="/studio" className="btn-primary mt-5 inline-flex">
                  Build a plan without a site <ArrowRight width={13} height={13} />
                </Link>
              </div>
            )}

            {data.skills && (
              <section aria-labelledby="skills-h">
                <h2 id="skills-h" className="mb-4 text-[18px] font-semibold tracking-[-0.01em] text-ink">
                  Your homepage, scored on the five skills
                </h2>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-4">
                  {SKILL_META.map(({ key, label, blurb }) => (
                    <SkillCard key={key} label={label} blurb={blurb} skill={data.skills!.skills[key]} />
                  ))}
                </div>
              </section>
            )}
            {!data.skills && data.route !== "dead" && (
              <div className="card max-w-[640px] p-5 text-[14px] leading-[1.6] text-ink-500">
                We mapped the site&apos;s structure, but the homepage itself couldn&apos;t be read
                {data.skills_unavailable_reason === "homepage_unreachable" ? " (it may be blocking robots)" : ""}
                — the full audit uses a real browser and usually gets through.
              </div>
            )}

            {data.packs.length > 0 && (
              <section aria-labelledby="packs-h">
                <h2 id="packs-h" className="mb-1 text-[18px] font-semibold tracking-[-0.01em] text-ink">
                  Your work, grouped into packs
                </h2>
                <p className="mb-4 max-w-[64ch] text-[13.5px] leading-[1.6] text-ink-300">
                  Ordered by expected impact — your homepage pack comes first.
                </p>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-4">
                  {data.packs.map((pack) => (
                    <div key={pack.pack_index} className={`card flex h-full flex-col gap-3 p-5 ${pack.locked ? "opacity-70" : ""}`}>
                      <div className="flex items-baseline justify-between gap-3">
                        <h3 className="text-[15px] font-semibold text-ink">
                          <span className="label-mono mr-2 !text-[10px] text-ink-300">
                            Pack {String(pack.pack_index).padStart(2, "0")}
                          </span>
                          {pack.title}
                        </h3>
                        {pack.locked && <span className="label-mono !text-[10px] text-ink-300">Locked</span>}
                      </div>
                      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                        {pack.pages.map((p) => (
                          <li key={p.url} className="truncate font-mono text-[12px] text-ink-500" title={p.url}>
                            {pathOf(p.url)}
                          </li>
                        ))}
                      </ul>
                      {pack.locked && (
                        <p className="m-0 mt-auto text-[12.5px] text-ink-300">Unlocks after your homepage pack.</p>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}

            <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,300px),1fr))] gap-4">
              {data.coverage && data.coverage.missing != null && data.coverage.missing > 0 && (
                <div className="card p-5">
                  <h2 className="text-[15px] font-semibold text-ink">What&apos;s missing</h2>
                  <p className="mt-1.5 text-[13.5px] leading-[1.6] text-ink-500">
                    {data.coverage.missing} recommended pages aren&apos;t on your site yet
                    {typeof data.coverage.pct === "number" ? ` (coverage ${Math.round(data.coverage.pct)}%)` : ""}.
                  </p>
                  {data.coverage.top_missing.length > 0 && (
                    <ul className="m-0 mt-3 flex list-none flex-col gap-1.5 p-0">
                      {data.coverage.top_missing.map((m) => (
                        <li key={m.slug ?? m.title ?? ""} className="truncate font-mono text-[12px] text-ink-500">
                          {m.title || m.slug}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <div className="card p-5">
                <h2 className="text-[15px] font-semibold text-ink">Competitors named on your site</h2>
                {data.competitors.names.length > 0 ? (
                  <ul className="m-0 mt-3 flex list-none flex-wrap gap-2 p-0">
                    {data.competitors.names.map((n) => (
                      <li key={n} className="rounded-full border border-white/15 px-3 py-1 text-[12.5px] text-ink-500">
                        {n}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1.5 text-[13.5px] leading-[1.6] text-ink-500">
                    None found on your pages — the full report compares you against real industry
                    peers instead.
                  </p>
                )}
              </div>
            </div>

            {data.route !== "dead" && (
              <div className="card flex flex-wrap items-center justify-between gap-4 p-6">
                <div>
                  <h2 className="text-[17px] font-semibold text-ink">Want the page-by-page plan?</h2>
                  <p className="mt-1 max-w-[52ch] text-[13.5px] leading-[1.6] text-ink-500">
                    The full audit reads every top page, ranks the highest-impact fixes, and builds
                    your interactive plan.
                  </p>
                </div>
                <a href={data.next.deeper} className="btn-primary shrink-0" onClick={() => api.track("overview_go_deeper", { domain: data.domain })}>
                  Go deeper — run the full audit <ArrowRight width={13} height={13} />
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
