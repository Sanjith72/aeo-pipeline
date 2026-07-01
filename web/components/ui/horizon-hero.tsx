"use client";

// HorizonHero — the signature above-the-fold moment: a scroll-driven flythrough where the camera
// flies forward through a monochrome cosmos while the headline crossfades through three product
// beats — ANSWER → VISIBLE → CHOSEN. Built as a SELF-CONTAINED scroll region (a tall section with a
// sticky 100vh stage), NOT a document-scroll trap, so TopBar's progress hairline and the page's
// other reveals keep working (README §5). Progress is read from this section's own rect; everything
// per-frame is done via refs + direct DOM writes (no React state) so it never re-renders on scroll.
//
// The WebGL cosmos is lazy-loaded with next/dynamic({ ssr:false }) so three.js stays out of the
// server bundle and the text/LCP paints before the canvas mounts; the instant-paint backdrop (glow +
// blueprint grid) covers the gap. By product decision this always animates — no reduced-motion
// static fallback — and it degrades gracefully if WebGL is missing.

import dynamic from "next/dynamic";
import { forwardRef, useEffect, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { EASE_OUT, Magnetic, m } from "@/components/motion/primitives";
import { ArrowRight } from "@/components/ui/icons";
import type { CamTarget } from "@/components/ui/horizon-cosmos";

const HorizonCosmos = dynamic(
  () => import("@/components/ui/horizon-cosmos").then((mod) => mod.HorizonCosmos),
  { ssr: false, loading: () => null },
);

// Camera keyframes the scroll flies between (HORIZON → COSMOS → INFINITY) and the scroll positions
// each beat is centered on. Exact values from the design prototype (README §9).
const CAM_POSITIONS: CamTarget[] = [
  { x: 0, y: 30, z: 300 },
  { x: 0, y: 40, z: -50 },
  { x: 0, y: 50, z: -700 },
];
const CENTERS = [0, 0.5, 1.0];
// The per-character title rise uses a slightly softer ease than the house EASE_OUT.
const CHAR_EASE = [0.2, 1, 0.3, 1] as const;

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const easeInOut = (t: number) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

export function HorizonHero() {
  const sectionRef = useRef<HTMLElement>(null);
  // Written here on scroll, read by the cosmos render loop — the camera target the scrub flies toward.
  const camTargetRef = useRef<CamTarget>({ ...CAM_POSITIONS[0] });
  const beatsRef = useRef<(HTMLElement | null)[]>([]);
  const fillRef = useRef<HTMLDivElement>(null);
  const counterRef = useRef<HTMLSpanElement>(null);
  const indicatorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;

    let raf = 0;
    const update = () => {
      raf = 0;
      // Progress 0→1 across THIS region only, from its own rect — never whole-document scroll.
      const rect = section.getBoundingClientRect();
      const denom = rect.height - window.innerHeight;
      const p = denom > 0 ? clamp(-rect.top / denom, 0, 1) : 0;

      // a) Camera target: eased-lerp between the two keyframes that bracket the current segment.
      const cp = CAM_POSITIONS;
      const seg = p * (cp.length - 1);
      const i = Math.min(Math.floor(seg), cp.length - 2);
      const f = easeInOut(seg - i);
      camTargetRef.current = {
        x: cp[i].x + (cp[i + 1].x - cp[i].x) * f,
        y: cp[i].y + (cp[i + 1].y - cp[i].y) * f,
        z: cp[i].z + (cp[i + 1].z - cp[i].z) * f,
      };

      // b) Beat crossfade by distance to each center, plus a small parallax drift. Only the active
      //    beat is clickable (the CTAs live on beat 01).
      beatsRef.current.forEach((el, k) => {
        if (!el) return;
        const o = Math.max(0, 1 - Math.abs(p - CENTERS[k]) / 0.5);
        el.style.opacity = Math.pow(o, 0.6).toFixed(3);
        el.style.pointerEvents = o > 0.55 ? "auto" : "none";
        el.style.transform = `translateY(${((CENTERS[k] - p) * 64).toFixed(1)}px)`;
      });

      // c) Readouts: fill tracks progress; counter = active beat; the whole indicator fades near the end.
      if (fillRef.current) fillRef.current.style.width = `${(p * 100).toFixed(1)}%`;
      const active = p < 0.25 ? 0 : p < 0.75 ? 1 : 2;
      if (counterRef.current) counterRef.current.textContent = `${String(active + 1).padStart(2, "0")} / 03`;
      if (indicatorRef.current) {
        indicatorRef.current.style.opacity = Math.max(0, 1 - Math.max(0, (p - 0.86) / 0.14)).toFixed(2);
      }
    };

    // Coalesce scroll/resize bursts into one rAF-batched DOM write per frame.
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };

    update(); // initial frame, so beat 01 is centered and the readouts are correct before any scroll
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  const setBeatRef = (k: number) => (el: HTMLElement | null) => {
    beatsRef.current[k] = el;
  };

  return (
    <section
      ref={sectionRef}
      // The tall scroll region gives the scrub its distance. Critically NO overflow clip here (or any
      // ancestor) — that would silently break `sticky` on the stage. The clip lives on the stage only.
      className="relative border-b border-ink/[0.06]"
      style={{ height: "340vh" }}
    >
      {/* the pinned stage — 100vh, full viewport, clipped, its own stacking context (isolate) so the
          hero's internal z-layers never compete with the sticky TopBar above it. */}
      <div className="grain sticky top-0 h-screen w-full overflow-hidden bg-paper isolate">
        {/* z0 — instant-paint backdrop: radial glow + faint blueprint grid, masked with a radial fade.
            Paints before WebGL so first paint never waits on three.js. */}
        <div className="pointer-events-none absolute inset-0 z-0" aria-hidden>
          <div
            className="absolute inset-0"
            style={{ background: "radial-gradient(ellipse 72% 60% at 50% 16%, rgba(255,255,255,0.09), transparent 70%)" }}
          />
          <div
            className="blueprint-grid absolute inset-0 opacity-50"
            style={{
              backgroundSize: "32px 32px",
              WebkitMaskImage: "radial-gradient(ellipse 78% 66% at 50% 28%, #000 32%, transparent 100%)",
              maskImage: "radial-gradient(ellipse 78% 66% at 50% 28%, #000 32%, transparent 100%)",
            }}
          />
        </div>

        {/* z1 — the cosmos (client-only; driven by the shared camera target) */}
        <HorizonCosmos camTargetRef={camTargetRef} className="pointer-events-none absolute inset-0 z-[1]" />

        {/* z2 — legibility scrims: radial vignette, a bottom fade, and a film-grain overlay */}
        <div
          className="pointer-events-none absolute inset-0 z-[2]"
          aria-hidden
          style={{ background: "radial-gradient(ellipse 92% 82% at 50% 50%, transparent 42%, rgba(10,10,12,0.5) 100%)" }}
        />
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 z-[2]"
          aria-hidden
          style={{ height: "32%", background: "linear-gradient(to top, #0a0a0c 4%, transparent)" }}
        />
        <div className="grain pointer-events-none absolute inset-0 z-[2]" aria-hidden style={{ mixBlendMode: "screen" }} />

        {/* z4 — content: three crossfading beats */}
        <div className="absolute inset-0 z-[4]">
          <Beat
            ref={setBeatRef(0)}
            as="h1"
            animate
            initialOpacity={1}
            maxWidth="40ch"
            eyebrow="01 — AI search visibility"
            title="ANSWER"
            ariaLabel="When customers ask AI, be the answer"
            subtitle={[
              <>When customers ask AI for a recommendation,</>,
              <>
                make sure the answer is <Accent>you</Accent>.
              </>,
            ]}
            ctas={
              <>
                <Magnetic>
                  <a href="#studio" className="btn-accent group !px-6 !py-3 text-[15px]">
                    Get my free plan
                    <ArrowRight className="transition-transform duration-200 group-hover:translate-x-0.5" />
                  </a>
                </Magnetic>
                <a href="#how" className="btn-ghost !px-6 !py-3 text-[15px]">
                  See how it works
                </a>
              </>
            }
          />
          <Beat
            ref={setBeatRef(1)}
            as="h2"
            initialOpacity={0}
            maxWidth="42ch"
            eyebrow="02 — Know where you stand"
            title="VISIBLE"
            ariaLabel="See how answer engines read your business"
            subtitle={[
              <>See exactly how answer engines read your</>,
              <>
                business — and the <Accent>score</Accent> they give it.
              </>,
            ]}
          />
          <Beat
            ref={setBeatRef(2)}
            as="h2"
            initialOpacity={0}
            maxWidth="42ch"
            eyebrow="03 — Be the recommendation"
            title="CHOSEN"
            ariaLabel="A plain-English plan to be the one AI recommends"
            subtitle={[
              <>A plain-English plan to become the one</>,
              <>
                AI recommends. There&apos;s no <Accent>page two</Accent>.
              </>,
            ]}
          />
        </div>

        {/* z6 — side menu (brand chrome): badge, vertical label, gradient tick. Slides in from the left.
            Hidden below ~480px so it never crowds the centered title. */}
        <div
          className="absolute z-[6] hidden min-[480px]:block"
          style={{ left: "clamp(16px, 3vw, 42px)", top: "50%", transform: "translateY(-50%)" }}
          aria-hidden
        >
          <m.div
            className="flex flex-col items-center gap-5"
            initial={{ x: -40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ duration: 0.9, delay: 0.2, ease: EASE_OUT }}
          >
            <span className="flex h-[30px] w-[30px] items-center justify-center rounded-[7px] bg-ink font-display text-[15px] font-bold leading-none text-paper">
              A
            </span>
            <span
              className="font-mono text-[11px] font-medium uppercase text-ink-300"
              style={{ writingMode: "vertical-rl", transform: "rotate(180deg)", letterSpacing: "0.34em" }}
            >
              AEO Studio
            </span>
            <span className="h-14 w-px" style={{ background: "linear-gradient(to bottom, rgba(255,255,255,0.4), transparent)" }} />
          </m.div>
        </div>

        {/* z6 — scroll-progress indicator (bottom center). Its overall opacity is driven by scroll;
            the CSS transition gives it a delayed entrance after the copy lands. */}
        <div
          ref={indicatorRef}
          className="absolute left-1/2 z-[6] flex flex-col items-center gap-3"
          style={{
            bottom: "clamp(20px, 4.5vh, 42px)",
            transform: "translateX(-50%)",
            opacity: 0,
            transition: "opacity 0.8s ease 1.2s",
          }}
          aria-hidden
        >
          <span className="animate-horizon-bob font-mono text-[10.5px] font-medium uppercase text-ink-300" style={{ letterSpacing: "0.3em" }}>
            Scroll
          </span>
          <div
            className="relative h-0.5 overflow-hidden rounded-full"
            style={{ width: "clamp(120px, 16vw, 184px)", background: "rgba(255,255,255,0.13)" }}
          >
            <div ref={fillRef} className="absolute left-0 top-0 h-full w-0 rounded-full bg-accent" />
          </div>
          <span ref={counterRef} className="font-mono text-[11px] font-medium text-ink-500" style={{ letterSpacing: "0.12em" }}>
            01 / 03
          </span>
        </div>
      </div>
    </section>
  );
}

// The single white serif-italic accent word in each subtitle (README §8).
function Accent({ children }: { children: ReactNode }) {
  return (
    <em className="font-serif italic text-accent" style={{ fontSize: "1.18em", padding: "0 0.04em" }}>
      {children}
    </em>
  );
}

// A transform-only entrance wrapper. On beat 01 it rises + fades in (staggered by `order`); on the
// other beats it renders a plain div (those beats only crossfade at the section level). Opacity
// settles at 1, so content is never gated behind a frozen timeline (README §12 gotcha 3).
function Anim({
  animate,
  order,
  className,
  style,
  children,
}: {
  animate: boolean;
  order: number;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  if (!animate) {
    return (
      <div className={className} style={style}>
        {children}
      </div>
    );
  }
  return (
    <m.div
      className={className}
      style={style}
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.9, delay: 0.48 + order * 0.15, ease: EASE_OUT }}
    >
      {children}
    </m.div>
  );
}

const TITLE_CLASS = "m-0 font-display font-semibold text-[clamp(2.6rem,11vw,9.5rem)] text-ink";
const TITLE_STYLE: CSSProperties = { letterSpacing: "-0.04em", lineHeight: 0.92, paddingBottom: "0.06em" };

// The giant title. On beat 01 each character rises into place; elsewhere it's static. Chars rest at
// opacity 1 with no clip, so the word is legible in any static snapshot or if motion can't advance.
function Title({ text, as, animate }: { text: string; as: "h1" | "h2"; animate: boolean }) {
  const Tag = as;
  if (!animate) {
    return (
      <Tag className={TITLE_CLASS} style={TITLE_STYLE}>
        {text}
      </Tag>
    );
  }
  return (
    <Tag className={TITLE_CLASS} style={TITLE_STYLE} aria-label={text}>
      {Array.from(text).map((ch, i) => (
        <m.span
          key={i}
          aria-hidden
          className="inline-block"
          style={{ willChange: "transform" }}
          initial={{ y: "0.26em", opacity: 0.4 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 1, delay: 0.24 + i * 0.055, ease: CHAR_EASE }}
        >
          {ch === " " ? " " : ch}
        </m.span>
      ))}
    </Tag>
  );
}

type BeatProps = {
  as: "h1" | "h2";
  eyebrow: string;
  title: string;
  ariaLabel: string;
  subtitle: ReactNode[];
  maxWidth: string;
  ctas?: ReactNode;
  /** Only beat 01 runs the on-load entrance; beats 02/03 simply crossfade in on scroll. */
  animate?: boolean;
  /** Resting opacity before scroll takes over — beat 01 starts visible, the others hidden. */
  initialOpacity: number;
};

// One crossfading beat. The SECTION's opacity/transform are owned by the scroll handler; the entrance
// (beat 01 only) animates the INNER elements, so the two never fight (README §11/§12).
const Beat = forwardRef<HTMLElement, BeatProps>(function Beat(
  { as, eyebrow, title, ariaLabel, subtitle, maxWidth, ctas, animate = false, initialOpacity },
  ref,
) {
  return (
    <section
      ref={ref}
      aria-label={ariaLabel}
      className="absolute inset-0 flex flex-col items-center justify-center text-center"
      style={{
        padding: "0 clamp(28px, 8vw, 110px)",
        gap: "clamp(20px, 3.4vh, 40px)",
        opacity: initialOpacity,
        willChange: "opacity, transform",
      }}
    >
      {/* eyebrow: a 26×1px rule + mono index */}
      <Anim animate={animate} order={0} className="flex items-center gap-3">
        <span aria-hidden className="h-px w-[26px]" style={{ background: "rgba(255,255,255,0.55)" }} />
        <span className="font-mono text-[11px] font-medium uppercase text-ink-500" style={{ letterSpacing: "0.2em" }}>
          {eyebrow}
        </span>
      </Anim>

      {/* the giant title — per-character staggered rise on beat 01 */}
      <Title text={title} as={as} animate={animate} />

      {/* two-line subtitle with one serif-italic accent word */}
      <Anim animate={animate} order={1} className="flex flex-col gap-0.5 text-ink-500" style={{ maxWidth }}>
        {subtitle.map((line, i) => (
          <p key={i} className="m-0 text-[clamp(1.02rem,2.3vw,1.45rem)]" style={{ lineHeight: 1.5 }}>
            {line}
          </p>
        ))}
      </Anim>

      {ctas && (
        <Anim
          animate={animate}
          order={2}
          className="flex flex-wrap items-center justify-center gap-3.5"
          style={{ marginTop: "clamp(4px, 1.2vh, 12px)" }}
        >
          {ctas}
        </Anim>
      )}
    </section>
  );
});

export default HorizonHero;
