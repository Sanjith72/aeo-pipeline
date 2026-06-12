"use client";

// Marketing chrome — top bar, hero, how-it-works, trust band, FAQ, footer. All copy
// is outcome language: what the owner gets, never how the pipeline works. Every
// entrance runs on the shared motion system (components/motion/primitives) so the
// whole page moves with one set of physics.

import { useState } from "react";
import type { ReactNode } from "react";
import {
  CountUp,
  EASE_OUT,
  Magnetic,
  Parallax,
  Reveal,
  Stagger,
  Item,
  TextReveal,
  Tilt,
  m,
  useReducedMotion,
  useScroll,
  useSpring,
} from "@/components/motion/primitives";
import { FAQ_ITEMS } from "@/lib/faq";
import { useScrolled } from "./ui/hooks";
import { ArrowRight, Sparkle } from "./ui/icons";

/** The "drawing number" stamped on every sheet — sections read as numbered
 *  blueprint pages: 01 hero, 02 how, 03 studio, 04 trust, 05 FAQ. */
export function SheetTag({ no, children }: { no: string; children: ReactNode }) {
  return (
    <span className="sheet-index inline-flex items-center gap-2.5">
      <span aria-hidden className="h-px w-6 bg-accent/50" />
      {no} / {children}
    </span>
  );
}

export function TopBar() {
  // The bar starts transparent over the hero grid, then morphs into a frosted,
  // shadowed surface as content scrolls beneath it. The hairline along its lower
  // edge tracks reading progress through the page — a spring chases the scroll
  // position so the line lands with weight instead of sticking to the thumb.
  const scrolled = useScrolled();
  const { scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 140, damping: 28, restDelta: 0.001 });
  return (
    <header
      className={`sticky top-0 z-40 border-b transition-all duration-300 ${
        scrolled ? "border-ink/[0.08] bg-paper-100/85 shadow-card backdrop-blur-md" : "border-transparent bg-paper/70 backdrop-blur-sm"
      }`}
    >
      <m.span
        aria-hidden
        className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-gradient-to-r from-accent to-accent-600"
        style={{ scaleX: progress, willChange: "transform" }}
      />
      <div
        className={`mx-auto flex max-w-6xl items-center justify-between px-5 transition-[padding] duration-300 ${
          scrolled ? "py-2.5" : "py-3.5"
        }`}
      >
        <a href="#" className="group flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-display text-sm font-bold text-paper-100 transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-105">
            A
          </span>
          <span className="font-display text-[15px] font-semibold tracking-tight">AEO Studio</span>
        </a>
        <nav className="flex items-center gap-1">
          <a href="#how" className="nav-link hidden sm:block">
            How it works
          </a>
          <a href="#faq" className="nav-link hidden sm:block">
            FAQ
          </a>
          <a href="#studio" className="btn-primary group !px-4 !py-2 text-[13px]">
            Get started
            <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" width={13} height={13} />
          </a>
        </nav>
      </div>
    </header>
  );
}

export function Hero() {
  // Reduced motion: the second headline line fades instead of rising out of its
  // line-box (a y:110% start inside overflow-hidden must never be the fallback).
  const reduced = useReducedMotion();
  const stats: [ReactNode, string][] = [
    // The one number on the page earns a count-up; the other stats stay still.
    [<CountUp key="min" to={5} suffix=" min" />, "from questions to plan"],
    ["Plain English", "no tech background needed"],
    ["Ready to use", "checklists your team can run with"],
  ];
  return (
    <section className="grain relative overflow-hidden border-b border-ink/[0.06]">
      <div className="blueprint-grid blueprint-grid-fade absolute inset-0" aria-hidden />
      {/* aurora: glow fields drifting behind the grid — the dark theme's pulse */}
      <div
        className="aurora pointer-events-none absolute -top-32 right-[-12%] h-[34rem] w-[34rem] rounded-full bg-accent/[0.16] blur-3xl"
        aria-hidden
      />
      <div
        className="aurora pointer-events-none absolute -bottom-40 left-[-10%] h-96 w-96 rounded-full bg-accent/[0.1] blur-3xl"
        aria-hidden
        style={{ animationDuration: "26s", animationDelay: "-9s" }}
      />

      {/* floating glass chip — layered depth: CSS float + scroll parallax (desktop only) */}
      <div className="pointer-events-none absolute right-[7%] top-28 hidden select-none lg:block" aria-hidden>
        <Parallax range={26}>
          <div className="glass animate-float-y-slow flex items-center gap-2 rounded-xl px-3.5 py-2.5 shadow-card">
            <Sparkle className="text-accent" />
            <span className="text-[13px] font-medium text-ink">The answer customers see first</span>
          </div>
        </Parallax>
      </div>

      <div className="relative mx-auto max-w-6xl px-5 py-20 sm:py-28">
        {/* orchestrated entrance: label → headline (word by word) → copy → CTAs → stats */}
        <div className="max-w-3xl">
          <Reveal y={12}>
            <SheetTag no="01">AI search visibility</SheetTag>
          </Reveal>
          <h1 className="mt-5 text-balance font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
            <TextReveal as="span" className="block" text="When customers ask AI," delay={0.1} />
            <span className="block">
              {/* the second line rises as one unit so the serif accent + underline stay glued */}
              <span className="inline-block overflow-hidden pb-2 align-bottom">
                <m.span
                  className="inline-block"
                  initial={reduced ? { opacity: 0 } : { y: "110%" }}
                  whileInView={reduced ? { opacity: 1 } : { y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: reduced ? 0.5 : 0.65, ease: EASE_OUT, delay: 0.32 }}
                >
                  be{" "}
                  <span className="relative inline-block font-serif italic tracking-[-0.01em] text-accent">
                    the answer
                    <span className="hero-underline absolute inset-x-0 -bottom-1 h-[3px] rounded-full bg-accent/30" aria-hidden />
                  </span>
                  .
                </m.span>
              </span>
            </span>
          </h1>
          <Reveal delay={0.25}>
            <p className="mt-6 max-w-2xl text-lg leading-relaxed text-ink-500">
              More customers now ask ChatGPT, Perplexity, and Google AI instead of scrolling through
              search results. AEO Studio tells you exactly what your website needs so AI assistants
              recommend <em className="not-italic text-ink">your</em> business first — no jargon, no guesswork.
            </p>
          </Reveal>
          <Reveal delay={0.35}>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Magnetic>
                <a href="#studio" className="btn-accent group !px-6 !py-3 text-[15px]">
                  Get my free plan
                  <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" />
                </a>
              </Magnetic>
              <a href="#how" className="btn-ghost !px-6 !py-3 text-[15px]">
                See how it works
              </a>
            </div>
          </Reveal>
        </div>

        <Reveal delay={0.45}>
          <dl className="mt-16 grid max-w-2xl grid-cols-1 gap-px overflow-hidden rounded-xl2 border border-ink/[0.08] bg-ink/[0.06] sm:grid-cols-3">
            {stats.map(([big, small]) => (
              <div key={small} className="group bg-paper-100 px-5 py-5 transition-colors duration-200 hover:bg-paper">
                <dt className="font-display text-xl font-semibold tabular-nums transition-colors duration-200 group-hover:text-accent sm:text-2xl">
                  {big}
                </dt>
                <dd className="mt-0.5 text-sm text-ink-500">{small}</dd>
              </div>
            ))}
          </dl>
        </Reveal>
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
          <SheetTag no="02">How it works</SheetTag>
          <h2 className="mt-2 max-w-xl text-2xl font-semibold sm:text-3xl">
            Three steps between you and being AI&apos;s{" "}
            <span className="font-serif italic text-accent">recommendation</span>
          </h2>
        </Reveal>
        <div className="relative mt-8">
          {/* the thread that ties the three steps together — draws in on reveal */}
          <m.div
            aria-hidden
            className="absolute left-[8%] right-[8%] top-7 hidden h-px origin-left bg-gradient-to-r from-accent/40 via-accent/15 to-accent/40 md:block"
            initial={{ scaleX: 0 }}
            whileInView={{ scaleX: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 1.1, ease: EASE_OUT, delay: 0.35 }}
          />
          <Stagger className="grid gap-5 md:grid-cols-3" stagger={0.12}>
            {steps.map(([num, title, body]) => (
              <Item key={num} className="h-full">
                <Tilt max={5} className="h-full">
                  <div className="group card relative h-full overflow-hidden p-6 transition-shadow duration-300 hover:shadow-lift">
                    <span className="relative z-10 inline-flex h-6 items-center rounded-full border border-accent/20 bg-accent-50 px-2 font-mono text-xs text-accent transition-colors duration-300 group-hover:border-accent/40">
                      {num}
                    </span>
                    <span
                      className="absolute -right-3 -top-5 font-display text-[88px] font-bold leading-none text-ink/[0.04] transition-transform duration-300 group-hover:scale-110"
                      aria-hidden
                    >
                      {num}
                    </span>
                    <h3 className="mt-3 text-base font-semibold">{title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
                  </div>
                </Tilt>
              </Item>
            ))}
          </Stagger>
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
          <SheetTag no="04">Why AEO Studio</SheetTag>
        </Reveal>
        <Stagger className="mt-6 grid gap-5 md:grid-cols-3" stagger={0.12}>
          {items.map(([title, body]) => (
            <Item key={title} className="h-full">
              <div className="group card h-full p-6 transition-all duration-300 hover:-translate-y-1 hover:shadow-lift">
                <div className="mb-4 h-9 w-9 rounded-lg border border-ink/10 bg-paper blueprint-grid transition-transform duration-300 group-hover:rotate-3" aria-hidden />
                <h3 className="text-base font-semibold">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-500">{body}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export function Faq() {
  // One question open at a time — the first starts open so the section never reads
  // as a wall of closed rows. Content comes from lib/faq.ts, the same source the
  // FAQPage JSON-LD in app/layout.tsx serializes, so the two can never drift.
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section id="faq" className="scroll-mt-20 border-b border-ink/[0.06]">
      <div className="mx-auto max-w-6xl px-5 py-16 sm:py-20">
        <div className="grid gap-10 md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] md:gap-14">
          <Reveal className="md:sticky md:top-28 md:self-start">
            <SheetTag no="05">Common questions</SheetTag>
            <h2 className="mt-2 text-2xl font-semibold sm:text-3xl">
              Answers, <span className="font-serif italic text-accent">before</span> you ask
            </h2>
            <p className="mt-3 max-w-md text-ink-500">
              The questions business owners ask us most — in the same plain English your plan
              arrives in.
            </p>
            <a href="#studio" className="group mt-6 inline-flex items-center gap-1.5 text-sm text-ink-500 transition-colors hover:text-accent">
              Still wondering? Get your free plan
              <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" width={13} height={13} />
            </a>
          </Reveal>

          <Stagger className="border-y border-ink/[0.08]" stagger={0.06}>
            {FAQ_ITEMS.map((item, i) => (
              <FaqRow
                key={item.q}
                index={i}
                question={item.q}
                answer={item.a}
                open={open === i}
                onToggle={() => setOpen((current) => (current === i ? null : i))}
              />
            ))}
          </Stagger>
        </div>
      </div>
    </section>
  );
}

function FaqRow({
  index,
  question,
  answer,
  open,
  onToggle,
}: {
  index: number;
  question: string;
  answer: string;
  open: boolean;
  onToggle: () => void;
}) {
  const panelId = `faq-panel-${index}`;
  const buttonId = `faq-button-${index}`;
  const reduced = useReducedMotion();
  return (
    <Item className="border-b border-ink/[0.08] last:border-b-0">
      <h3>
        <button
          id={buttonId}
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
          className="group flex w-full items-start justify-between gap-4 py-5 text-left"
        >
          <span className="flex items-baseline gap-4">
            <span className="font-mono text-[11px] text-accent/70">{String(index + 1).padStart(2, "0")}</span>
            <span
              className={`text-[15px] font-medium transition-colors duration-200 ${
                open ? "text-ink" : "text-ink-500 group-hover:text-ink"
              }`}
            >
              {question}
            </span>
          </span>
          {/* a mono plus that turns into a close — the technical voice, no icon set needed */}
          <m.span
            aria-hidden
            animate={{ rotate: open ? 45 : 0 }}
            transition={{ duration: 0.3, ease: EASE_OUT }}
            className={`mt-0.5 shrink-0 font-mono text-lg leading-none transition-colors duration-200 ${
              open ? "text-accent" : "text-ink-300 group-hover:text-accent"
            }`}
          >
            +
          </m.span>
        </button>
      </h3>
      {/* The panel stays mounted so aria-controls always points at a real node
          (ARIA APG accordion pattern); height collapses to 0 and aria-hidden
          takes it out of the accessibility tree while closed. */}
      <m.div
        id={panelId}
        role="region"
        aria-labelledby={buttonId}
        aria-hidden={!open}
        initial={false}
        animate={{ height: open ? "auto" : 0, opacity: open ? 1 : 0 }}
        transition={{ duration: reduced ? 0 : 0.45, ease: EASE_OUT }}
        className="overflow-hidden"
      >
        <p className="max-w-2xl pb-6 pl-[38px] text-sm leading-relaxed text-ink-500">{answer}</p>
      </m.div>
    </Item>
  );
}

export function Footer() {
  return (
    <footer className="relative border-t border-ink/[0.06]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/25 to-transparent" aria-hidden />
      <Reveal y={12} className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-3 px-5 py-8 text-sm text-ink-300 sm:flex-row sm:items-center">
        <a href="#" className="group flex items-center gap-2 font-display font-semibold text-ink-500 transition-colors hover:text-ink">
          <span className="flex h-5 w-5 items-center justify-center rounded bg-ink font-display text-[10px] font-bold text-paper-100 transition-transform duration-200 group-hover:-rotate-6">
            A
          </span>
          AEO Studio
        </a>
        <span className="label-mono !tracking-[0.1em]">Be the answer customers find</span>
      </Reveal>
    </footer>
  );
}
