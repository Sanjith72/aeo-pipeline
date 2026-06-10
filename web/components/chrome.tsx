"use client";

// Marketing chrome — top bar, hero, how-it-works, trust band, footer. All copy is
// outcome language: what the owner gets, never how the pipeline works.

import { Reveal } from "./ui/Reveal";
import { ArrowRight } from "./ui/icons";

export function TopBar() {
  return (
    <header className="sticky top-0 z-40 border-b border-ink/[0.06] bg-paper/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3.5">
        <a href="#" className="group flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-display text-sm font-bold text-paper-100 transition-transform duration-200 group-hover:-rotate-6">
            A
          </span>
          <span className="font-display text-[15px] font-semibold tracking-tight">AEO Studio</span>
        </a>
        <nav className="flex items-center gap-1">
          <a href="#how" className="hidden rounded-lg px-3 py-2 text-[13px] text-ink-500 transition-colors hover:text-ink sm:block">
            How it works
          </a>
          <a href="#studio" className="btn-primary !px-4 !py-2 text-[13px]">
            Get started
          </a>
        </nav>
      </div>
    </header>
  );
}

export function Hero() {
  const stats: [string, string][] = [
    ["5 min", "from questions to plan"],
    ["Plain English", "no tech background needed"],
    ["Ready to use", "checklists your team can run with"],
  ];
  return (
    <section className="relative overflow-hidden border-b border-ink/[0.06]">
      <div className="blueprint-grid blueprint-grid-fade absolute inset-0" aria-hidden />
      <div
        className="pointer-events-none absolute -top-24 right-[-10%] h-96 w-96 rounded-full bg-accent/[0.07] blur-3xl"
        aria-hidden
      />
      <div className="relative mx-auto max-w-6xl px-5 py-20 sm:py-28">
        <div className="max-w-3xl animate-fade-up">
          <span className="label-mono inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            AI search visibility
          </span>
          <h1 className="mt-5 text-balance font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            When customers ask AI,
            <br />
            be <span className="relative inline-block text-accent">the answer<span className="hero-underline absolute inset-x-0 -bottom-1 h-[3px] rounded-full bg-accent/30" aria-hidden /></span>.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-500">
            More customers now ask ChatGPT, Perplexity, and Google AI instead of scrolling through
            search results. AEO Studio tells you exactly what your website needs so AI assistants
            recommend <em className="not-italic text-ink">your</em> business first — no jargon, no guesswork.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a href="#studio" className="btn-accent group !px-6 !py-3 text-[15px]">
              Get my free plan
              <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" />
            </a>
            <a href="#how" className="btn-ghost !px-6 !py-3 text-[15px]">
              See how it works
            </a>
          </div>
        </div>

        <dl className="mt-16 grid max-w-2xl grid-cols-1 gap-px overflow-hidden rounded-xl2 border border-ink/[0.08] bg-ink/[0.06] animate-fade-up-slow sm:grid-cols-3">
          {stats.map(([big, small]) => (
            <div key={small} className="bg-paper-100 px-5 py-5 transition-colors duration-200 hover:bg-paper">
              <dt className="font-display text-xl font-semibold sm:text-2xl">{big}</dt>
              <dd className="mt-0.5 text-sm text-ink-500">{small}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

export function HowItWorks() {
  const steps: [string, string, string][] = [
    [
      "01",
      "Tell us about your business",
      "Five quick questions — your name, your goals, your competitors. We even suggest the competitors for you.",
    ],
    [
      "02",
      "We do the analysis",
      "We look at how AI assistants see your business today and where customers are slipping away to competitors.",
    ],
    [
      "03",
      "You get a plan that works",
      "A prioritized to-do list in plain English, plus ready-made files your web person can use the same day.",
    ],
  ];
  return (
    <section id="how" className="scroll-mt-20 border-b border-ink/[0.06]">
      <div className="mx-auto max-w-6xl px-5 py-16 sm:py-20">
        <Reveal>
          <span className="label-mono">How it works</span>
          <h2 className="mt-2 max-w-xl text-2xl font-semibold sm:text-3xl">
            Three steps between you and being AI's recommendation
          </h2>
        </Reveal>
        <div className="mt-8 grid gap-5 md:grid-cols-3">
          {steps.map(([num, title, body], i) => (
            <Reveal key={num} delay={i * 120}>
              <div className="group card relative h-full overflow-hidden p-6 transition-all duration-300 hover:-translate-y-1 hover:shadow-lift">
                <span className="font-mono text-xs text-accent">{num}</span>
                <span
                  className="absolute -right-3 -top-5 font-display text-[88px] font-bold leading-none text-ink/[0.04] transition-transform duration-300 group-hover:scale-110"
                  aria-hidden
                >
                  {num}
                </span>
                <h3 className="mt-3 text-base font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function TrustBand() {
  const items: [string, string][] = [
    [
      "Real analysis, not guesswork",
      "Your plan is built from how AI assistants actually choose what to recommend — every step is there for a reason.",
    ],
    [
      "Your information stays yours",
      "Your business details are used only to build your plan — never shared, sold, or sent anywhere else.",
    ],
    [
      "Results you can act on today",
      "No reports that sit in a drawer. You get a checklist, page outlines, and files your team can use immediately.",
    ],
  ];
  return (
    <section className="border-y border-ink/[0.06] bg-paper-200/60">
      <div className="mx-auto max-w-6xl px-5 py-16">
        <Reveal>
          <span className="label-mono">Why businesses choose AEO Studio</span>
        </Reveal>
        <div className="mt-6 grid gap-5 md:grid-cols-3">
          {items.map(([title, body], i) => (
            <Reveal key={title} delay={i * 120}>
              <div className="group card h-full p-6 transition-all duration-300 hover:-translate-y-1 hover:shadow-lift">
                <div className="mb-4 h-9 w-9 rounded-lg border border-ink/10 bg-paper blueprint-grid transition-transform duration-300 group-hover:rotate-3" aria-hidden />
                <h3 className="text-base font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-ink/[0.06]">
      <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-3 px-5 py-8 text-sm text-ink-300 sm:flex-row sm:items-center">
        <span className="font-display font-semibold text-ink-500">AEO Studio</span>
        <span className="label-mono !tracking-[0.1em]">Be the answer customers find</span>
      </div>
    </footer>
  );
}
