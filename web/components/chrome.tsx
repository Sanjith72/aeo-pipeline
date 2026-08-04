"use client";

// Marketing chrome — top bar, hero, how-it-works, trust band, FAQ, footer. All copy
// is outcome language: what the owner gets, never how the pipeline works. Every
// entrance runs on the shared motion system (components/motion/primitives) so the
// whole page moves with one set of physics.

import { Fragment, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import {
  EASE_OUT,
  Reveal,
  Stagger,
  Item,
  m,
  staggerContainer,
  useReducedMotion,
  useScroll,
  useSpring,
} from "@/components/motion/primitives";
import { FAQ_ITEMS } from "@/lib/faq";
import { useAuth } from "./auth/AuthProvider";
import { HorizonHero } from "./ui/horizon-hero";
import { useScrolled } from "./ui/hooks";
import { ArrowRight, ChartUp, DocLines, ShieldCheck } from "./ui/icons";

/** The section kicker — a 26px rule that draws in beside "0N — Label" in the mono
 *  measurement voice. Sections read as numbered sheets: 01 hero, 02 how, 03 studio,
 *  04 why, 05 FAQ. */
export function SheetTag({ no, children }: { no: string; children: ReactNode }) {
  return (
    <span className="sheet-index inline-flex items-center gap-3">
      <m.span
        aria-hidden
        className="h-px w-[26px] origin-left bg-white/55"
        initial={{ scaleX: 0, opacity: 0 }}
        whileInView={{ scaleX: 1, opacity: 1 }}
        viewport={{ once: true }}
        transition={{ duration: 0.8, ease: EASE_OUT }}
      />
      {no}
      {" — "}
      {children}
    </span>
  );
}

// ── display headline ──────────────────────────────────────────────────────────
// Section h2s at the handoff's display scale: every word rises 0.65em out of the
// baseline (850ms, 60ms stagger, a slightly softer ease than the house EASE_OUT)
// with ONE serif-italic accent word per heading. Reduced motion gets a plain fade.

const WORD_EASE = [0.2, 1, 0.3, 1] as const;

function RisingWord({ children, className = "" }: { children: ReactNode; className?: string }) {
  const reduced = useReducedMotion();
  return (
    <m.span
      className={`inline-block will-change-transform ${className}`}
      variants={
        reduced
          ? { hidden: { opacity: 0 }, visible: { opacity: 1, transition: { duration: 0.5, ease: EASE_OUT } } }
          : {
              hidden: { y: "0.65em", opacity: 0 },
              visible: { y: 0, opacity: 1, transition: { duration: 0.85, ease: WORD_EASE } },
            }
      }
    >
      {children}
    </m.span>
  );
}

/** The big section heading: `lead` words, then the serif-italic `accent` word, then an
 *  optional `trail` (e.g. a closing period that must hug the accent word). */
export function DisplayH2({
  lead,
  accent,
  trail,
  className = "",
}: {
  lead: string;
  accent: string;
  trail?: string;
  className?: string;
}) {
  return (
    <m.h2
      className={`max-w-[24ch] font-semibold text-ink ${className}`}
      style={{ fontSize: "clamp(2.1rem, 4.8vw, 3.8rem)", lineHeight: 1.08, letterSpacing: "-0.035em" }}
      variants={staggerContainer(0.06)}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "0px 0px -6% 0px" }}
    >
      {lead.split(" ").map((word, i) => (
        <Fragment key={`${word}-${i}`}>
          <RisingWord>{word}</RisingWord>{" "}
        </Fragment>
      ))}
      <RisingWord className="pr-[0.08em]">
        <em className="word-accent">{accent}</em>
      </RisingWord>
      {trail ? <RisingWord>{trail}</RisingWord> : null}
    </m.h2>
  );
}

export function TopBar() {
  // A tinted bar — deliberately NOT frosted glass any more.
  //
  // This used to carry backdrop-blur (lg scrolled, md at rest) plus a backdrop-saturate, on
  // the reasoning that "the bar is only ~50px tall, so it re-blurs a thin strip, not the whole
  // viewport — cheap during scroll". Cheap it may have been, but the visible result is the
  // reported defect: a backdrop-filter samples whatever is behind it every frame, so text and
  // images passing underneath during a scroll smear through the bar. That reads as the page
  // blurring as you scroll down, which is exactly what it is.
  //
  // The fix is opacity instead of blur: once scrolled, the bar is essentially SOLID, so
  // content passing beneath is hidden rather than smeared. Unscrolled it stays a light tint so
  // the hero still reads through at the top of the page, where nothing is passing under it yet
  // and there is nothing to smear. The glass EDGE is preserved — the hairline border and the
  // reading-progress line below are untouched, so the bar keeps its shape and weight.
  //
  // Modal backdrops (AuthModal, UnlockModal, CheckpointModal, TaskDetailPanel) keep their
  // blur on purpose: they sit over frozen content, so nothing moves beneath them to smear.
  const scrolled = useScrolled();
  const { scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 140, damping: 28, restDelta: 0.001 });
  return (
    <header
      className={`sticky top-0 z-40 border-b transition-all duration-300 ${
        scrolled
          ? "border-white/10 bg-paper/95 shadow-card"
          : "border-white/[0.08] bg-paper/30"
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
        <Link href="/" className="group flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-display text-sm font-bold text-paper-100 transition-transform duration-300 group-hover:-rotate-6 group-hover:scale-105">
            A
          </span>
          <span className="font-display text-[15px] font-semibold tracking-tight">AEO Studio</span>
        </Link>
        <nav className="flex items-center gap-1">
          {/* /#how and /#faq (not bare hashes): the bar also renders on /studio and
              /plan/<id>, where an in-page anchor would go nowhere. */}
          <Link href="/#how" className="nav-link hidden sm:block">
            How it works
          </Link>
          <Link href="/#faq" className="nav-link hidden sm:block">
            FAQ
          </Link>
          <a href="/agents" className="nav-link hidden sm:block">
            Agent Review
          </a>
          {/* The "Get started" CTA that used to sit here is gone (Phase 3 item 3.1). Users
              enter through the hero's "Analyze my site" field instead, which starts the real
              flow with their domain already captured — a header link to a bare /studio asked
              them to type it a second time. /studio is still reachable from HowItWorks ("Get
              your real report") and the FAQ ("Still wondering? Get your free plan"), so no
              route is orphaned. */}
          <AccountSlot />
        </nav>
      </div>
    </header>
  );
}

// The hero is now the scroll-driven cosmic flythrough (components/ui/horizon-hero): a sticky 100vh
// stage where the camera flies through a monochrome starfield while the headline crossfades
// ANSWER → VISIBLE → CHOSEN. The WebGL piece is lazy-loaded there (next/dynamic, ssr:false) so
// three.js stays out of the server bundle and the text/LCP paints before the canvas mounts. Kept as
// a thin wrapper so page.tsx's <Hero /> integration point is unchanged.
export function Hero() {
  return <HorizonHero />;
}

// v5 CH-07 — the account slot in the top bar. Degradable: renders NOTHING when Supabase
// auth isn't configured, so the marketing chrome is unchanged without env.
function AccountSlot() {
  const { authEnabled, user, openAuth, signOut } = useAuth();
  if (!authEnabled) return null;
  // Never `hidden` on small screens. This slot is the ONLY way into the account on any
  // route, so hiding it below 640px doesn't declutter the header — it removes sign-in
  // entirely on a phone, which is indistinguishable from "sign-in is broken". Shrink
  // instead: drop the email text (the long part) and keep the controls at every width.
  if (user) {
    return (
      <div className="flex items-center gap-2">
        <span
          className="hidden max-w-[16ch] truncate text-[12.5px] text-ink-300 sm:inline"
          title={user.email ?? ""}
        >
          {user.email}
        </span>
        <button type="button" onClick={() => void signOut()} className="nav-link">
          Sign out
        </button>
      </div>
    );
  }
  return (
    <button type="button" onClick={() => openAuth()} className="nav-link">
      Sign in
    </button>
  );
}

// The canonical five outcome skills, shared by the landing preview + "what you get" strip
// (mirrors OverviewView.SKILL_META so the marketing copy and the real product agree).
const SKILLS: { label: string; blurb: string }[] = [
  { label: "Messaging", blurb: "Is it instantly clear what you do, who it’s for, and why it matters?" },
  { label: "Conversion", blurb: "Is there an obvious next step for a ready buyer — and a path for the undecided?" },
  { label: "Discovery & Visibility", blurb: "Can people — and AI answer engines — find and read this page?" },
  { label: "Proof & Trust", blurb: "Do you show evidence a stranger would actually believe?" },
  { label: "Structure & UX", blurb: "Does the page read in clean, scannable chunks?" },
];

// A static sample "report" for the landing preview (CH-11c). Deliberately hardcoded — the
// hero must never call the API. Scores/fixes are plausible illustrations, not a real audit.
const SAMPLE = {
  overall: 62,
  scores: [78, 54, 44, 40, 71], // one per SKILLS entry, in order
  fixes: [
    "Rewrite the H1 to say what you do and who it’s for, in plain words.",
    "Add one clear primary call-to-action a ready buyer can act on.",
    "Add a short FAQ: 3–5 real customer questions, each answered in 2–3 sentences.",
  ],
};

/** Length-encoded 0–100 meter in house monochrome (track white/10, fill accent) — the same
 *  mark the real overview uses; carries role/aria so state is never color-only. */
function Meter({ value, label }: { value: number; label: string }) {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
      role="img"
      aria-label={`${label}: ${value} out of 100`}
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

// CH-11c — a real result PREVIEW near the hero, so a first-time visitor sees the deliverable
// (a scored report + ranked fixes) before committing. Static; no API call, no image.
export function ReportPreview() {
  return (
    <section className="relative" style={{ padding: "clamp(70px, 10vh, 120px) 0 clamp(40px, 6vh, 70px)" }}>
      <div className="mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
        <Reveal>
          <SheetTag no="02">Your report</SheetTag>
        </Reveal>
        <DisplayH2 lead="A real plan, not a" accent="pitch" trail="." className="mb-[clamp(30px,5vh,48px)] mt-[26px]" />
        <Reveal>
          <div className="card mx-auto max-w-[720px] p-6 sm:p-8">
            <div className="mb-6 flex items-baseline justify-between gap-4">
              <div>
                <span className="label-mono !text-[10px] text-ink-300">Overall</span>
                <div className="font-display text-[44px] font-semibold leading-none text-accent">{SAMPLE.overall}</div>
              </div>
              <span className="label-mono !text-[10px] text-ink-300">sample report · yoursite.com</span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {SKILLS.map((s, i) => (
                <div key={s.label} className="flex flex-col gap-1.5">
                  <div className="flex items-baseline justify-between">
                    <span className="text-[13px] font-medium text-ink">{s.label}</span>
                    <span className="font-mono text-[12px] text-ink-500">{SAMPLE.scores[i]}</span>
                  </div>
                  <Meter value={SAMPLE.scores[i]} label={s.label} />
                </div>
              ))}
            </div>
            <h3 className="mb-2 mt-6 text-[13px] font-semibold text-ink">Fix these first</h3>
            <ol className="m-0 flex list-none flex-col gap-2 p-0">
              {SAMPLE.fixes.map((fix, i) => (
                <li key={i} className="flex items-start gap-3">
                  <span className="font-display text-[14px] font-semibold text-accent">{String(i + 1).padStart(2, "0")}</span>
                  <span className="text-[13.5px] leading-[1.5] text-ink-500">{fix}</span>
                </li>
              ))}
            </ol>
          </div>
        </Reveal>
        <div className="mt-7 text-center">
          <Link href="/studio" className="btn-primary group inline-flex">
            Get your real report
            <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" width={13} height={13} />
          </Link>
        </div>
      </div>
    </section>
  );
}

// CH-11d — the concrete "what you get": the five skills we score, each with a one-line
// plain-English promise. Sits between the preview and how-it-works.
export function WhatYouGet() {
  return (
    <section className="relative" style={{ padding: "clamp(60px, 8vh, 100px) 0 clamp(40px, 6vh, 70px)" }}>
      <div className="mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
        <Reveal className="mb-[clamp(30px,5vh,48px)]">
          <SheetTag no="03">What you get</SheetTag>
        </Reveal>
        <Stagger className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,220px),1fr))] gap-[18px]" stagger={0.1}>
          {SKILLS.map((s) => (
            <Item key={s.label} className="h-full">
              <div className="h-full rounded-[18px] border border-white/[0.09] bg-white/[0.022] px-6 pb-7 pt-6">
                <h3 className="mb-2 text-[16px] font-semibold tracking-[-0.01em] text-accent">{s.label}</h3>
                <p className="m-0 text-[14px] leading-[1.6] text-ink-500">{s.blurb}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export function HowItWorks() {
  // CH-11e — the concrete four steps of the actual product (paste URL → overview → pack
  // → fix & re-verify), replacing the old generic "five questions" framing.
  const steps: [string, string, string][] = [
    [
      "01",
      "Enter your website",
      "One field, no signup. Paste your address and we get to work — nothing else to fill in.",
    ],
    [
      "02",
      "We see your site the way AI does",
      "How answer engines like ChatGPT and Perplexity read and rank your business today.",
    ],
    [
      "03",
      "Get your scorecard + ranked fixes",
      "Five skills scored 0–100, with the highest-impact fixes surfaced first — not a 500-item dump.",
    ],
    [
      "04",
      "Hand it off and ship",
      "A plain-English checklist plus ready-made files your web person can use the same day — then re-check to prove the lift.",
    ],
  ];
  return (
    <section id="how" className="relative scroll-mt-20" style={{ padding: "clamp(100px, 15vh, 170px) 0 clamp(70px, 9vh, 110px)" }}>
      {/* radial glow crowning the section (design §2) */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-0 h-[60%] w-[120%] -translate-x-1/2"
        style={{ background: "radial-gradient(ellipse 50% 55% at 50% 0%, rgba(255,255,255,0.055), transparent 70%)" }}
      />
      <div className="relative mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
        <Reveal>
          <SheetTag no="04">How it works</SheetTag>
        </Reveal>
        <DisplayH2
          lead="Four steps between you and being AI’s"
          accent="recommendation"
          className="mb-[clamp(44px,7vh,72px)] mt-[26px]"
        />
        <Stagger className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-[18px]" stagger={0.13}>
          {steps.map(([num, title, body]) => (
            <Item key={num} className="h-full">
              <div
                className="relative h-full overflow-hidden rounded-[18px] border border-white/[0.09] px-7 pb-[34px] pt-[30px] transition-[transform,border-color] duration-[350ms] ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-[5px] hover:border-white/[0.22]"
                style={{ background: "linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.012))" }}
              >
                {/* the 150px ghost numeral, clipped by the card */}
                <span
                  aria-hidden
                  className="pointer-events-none absolute -top-[34px] right-[-8px] select-none font-display text-[150px] font-bold leading-none tracking-[-0.06em] text-white/[0.045]"
                >
                  {num}
                </span>
                <span className="mb-[46px] inline-flex items-center justify-center rounded-full border border-white/20 px-3 py-1.5 font-mono text-[11px] font-medium tracking-[0.1em] text-ink">
                  {num}
                </span>
                <h3 className="mb-3 text-[21px] font-semibold tracking-[-0.015em] text-accent">{title}</h3>
                <p className="text-[15px] leading-[1.65] text-ink-500">{body}</p>
              </div>
            </Item>
          ))}
        </Stagger>
      </div>
    </section>
  );
}

export function TrustBand() {
  // CH-11g — the band names WHO it's for (founders of $5–30M sales-led B2B, in-house
  // marketers, agencies) instead of abstract properties. Honesty kept: no fabricated logos.
  const items = [
    {
      Icon: ChartUp,
      title: "Founders of $5–30M B2B",
      body: "You don’t have time to decode AI search. Get the plan and the ranked fixes, not the theory — and proof each one moved the needle.",
    },
    {
      Icon: DocLines,
      title: "In-house marketers",
      body: "A prioritized, defensible backlog you can take straight to your team — with a before/after score behind every item.",
    },
    {
      Icon: ShieldCheck,
      title: "Agencies",
      body: "Client-ready page outlines and files you can white-label and ship. Your clients’ details stay theirs — never shared or sold.",
    },
  ];
  return (
    <section className="relative" style={{ padding: "clamp(80px, 11vh, 130px) 0 clamp(50px, 7vh, 90px)" }}>
      <div className="mx-auto max-w-[1240px]" style={{ padding: "0 clamp(24px, 5vw, 64px)" }}>
        <Reveal className="mb-[clamp(34px,5vh,50px)]">
          <SheetTag no="05">Who it’s for</SheetTag>
        </Reveal>
        <Stagger className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,280px),1fr))] gap-[18px]" stagger={0.13}>
          {items.map(({ Icon, title, body }) => (
            <Item key={title} className="h-full">
              <div className="h-full rounded-[18px] border border-white/[0.09] bg-white/[0.022] px-7 pb-8 pt-7 transition-[transform,border-color] duration-[350ms] ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-1 hover:border-white/20">
                <span
                  aria-hidden
                  className="mb-[22px] flex h-11 w-11 items-center justify-center rounded-xl border border-white/[0.13] bg-white/[0.05] text-ink"
                >
                  <Icon />
                </span>
                <h3 className="mb-2.5 text-[19px] font-semibold tracking-[-0.01em] text-accent">{title}</h3>
                <p className="text-[14.5px] leading-[1.65] text-ink-500">{body}</p>
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
  // FAQPage JSON-LD in app/page.tsx serializes, so the two can never drift.
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section id="faq" className="scroll-mt-20" style={{ padding: "clamp(70px, 10vh, 120px) 0 clamp(110px, 16vh, 180px)" }}>
      <div
        className="mx-auto grid max-w-[1240px] grid-cols-[repeat(auto-fit,minmax(min(100%,320px),1fr))] items-start gap-[clamp(40px,6vw,90px)]"
        style={{ padding: "0 clamp(24px, 5vw, 64px)" }}
      >
        <Reveal className="md:sticky md:top-28 md:self-start">
          <SheetTag no="06">Common questions</SheetTag>
          <h2
            className="mt-[26px] font-semibold text-ink"
            style={{ fontSize: "clamp(2rem, 3.8vw, 3.2rem)", lineHeight: 1.08, letterSpacing: "-0.035em" }}
          >
            Answers, <em className="word-accent">before</em> you ask
          </h2>
          <p className="mt-[18px] max-w-[38ch] text-[15.5px] leading-[1.65] text-ink-500">
            The questions business owners ask us most — in the same plain English your plan
            arrives in.
          </p>
          <Link
            href="/studio"
            className="mt-[30px] inline-flex items-center gap-[9px] border-b border-white/30 pb-[3px] text-[15px] font-medium text-ink transition-[gap,border-color] duration-[250ms] ease-out hover:gap-[14px] hover:border-white/90"
          >
            Still wondering? Get your free plan <span aria-hidden>&rarr;</span>
          </Link>
        </Reveal>

        <Stagger className="border-t border-white/[0.09]" stagger={0.06}>
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
    <Item className="border-b border-white/[0.09]">
      <h3 className="m-0">
        <button
          id={buttonId}
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={onToggle}
          className="group flex w-full items-baseline gap-[18px] px-0.5 py-[23px] text-left transition-colors duration-200 hover:bg-white/[0.015]"
        >
          <span aria-hidden className="shrink-0 font-mono text-[11px] font-medium text-[#6f6f77]">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span
            className={`flex-1 font-display text-[17.5px] font-medium tracking-[-0.01em] transition-colors duration-[250ms] ${
              open ? "text-accent" : "text-ink-700 group-hover:text-ink"
            }`}
          >
            {question}
          </span>
          {/* a plus that turns into a close — the technical voice, no icon set needed */}
          <m.span
            aria-hidden
            animate={{ rotate: open ? 45 : 0 }}
            transition={{ duration: 0.4, ease: EASE_OUT }}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center self-center text-[17px] font-normal leading-none text-ink-500"
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
        transition={{ duration: reduced ? 0 : 0.55, ease: EASE_OUT }}
        className="overflow-hidden"
      >
        <p className="m-0 px-10 pb-[26px] text-[15px] leading-[1.7] text-ink-500">{answer}</p>
      </m.div>
    </Item>
  );
}

export function Footer() {
  return (
    <footer className="relative border-t border-ink/[0.06]">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/25 to-transparent" aria-hidden />
      <Reveal y={12} className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-3 px-5 py-8 text-sm text-ink-300 sm:flex-row sm:items-center">
        <Link href="/" className="group flex items-center gap-2 font-display font-semibold text-ink-500 transition-colors hover:text-ink">
          <span className="flex h-5 w-5 items-center justify-center rounded bg-ink font-display text-[10px] font-bold text-paper-100 transition-transform duration-200 group-hover:-rotate-6">
            A
          </span>
          AEO Studio
        </Link>
        <span className="label-mono !tracking-[0.1em]">Be the answer customers find</span>
      </Reveal>
    </footer>
  );
}
