"use client";

// The motion system — one shared vocabulary for the whole site, built on
// framer-motion. LazyMotion + `m` keeps the runtime lean (domMax is loaded once,
// here, and tree-shaken everywhere else); MotionConfig honors reduced-motion at
// the root so no component has to remember to. Sections compose these primitives
// instead of hand-rolling springs, so every entrance on the page shares the same
// physics and the site moves like one instrument, not twelve.

import {
  AnimatePresence,
  LazyMotion,
  MotionConfig,
  animate,
  domMax,
  m,
  useInView,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
} from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

// Re-exports so sections import everything motion from one place (LazyMotion
// strict mode forbids the full `motion` import — always use `m` from here).
export {
  AnimatePresence,
  animate,
  m,
  useInView,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
};
export { useMotionTemplate, useMotionValueEvent } from "framer-motion";

// ── root provider ─────────────────────────────────────────────────────────────

/** Wraps the page once. `strict` guarantees nothing imports the full `motion`
 *  bundle by accident; `reducedMotion="user"` turns transform/layout motion off
 *  for users who asked for less (opacity fades remain, so nothing pops). */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}

// ── shared physics ────────────────────────────────────────────────────────────

/** The house easing — a confident decelerate used by every entrance. */
export const EASE_OUT = [0.16, 1, 0.3, 1] as const;

/** Standard entrance: rise + de-blur, like focus arriving. */
export const fadeUp = {
  hidden: { opacity: 0, y: 24, filter: "blur(8px)" },
  visible: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { duration: 0.7, ease: EASE_OUT },
  },
};

/** The reduced-motion counterpart: a plain fade. MotionConfig only nulls
 *  transform/layout animations, so filter (blur) would otherwise still play —
 *  components pick variants via useFadeUp() so reduced-motion users get
 *  opacity only, with no dependence on framework internals. */
export const fadeUpReduced = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.5, ease: EASE_OUT } },
};

/** fadeUp that honors prefers-reduced-motion. Use inside components only. */
export function useFadeUp() {
  const reduced = useReducedMotion();
  return reduced ? fadeUpReduced : fadeUp;
}

/** Container that staggers its `fadeUp` children. */
export const staggerContainer = (stagger = 0.08, delayChildren = 0) => ({
  hidden: {},
  visible: { transition: { staggerChildren: stagger, delayChildren } },
});

// ── scroll-triggered reveal ───────────────────────────────────────────────────

/** Reveals once when scrolled into view. Drop-in for any block.
 *  `delay` staggers siblings; `y` tunes the rise distance. */
export function Reveal({
  children,
  delay = 0,
  y = 24,
  className,
  once = true,
}: {
  children: ReactNode;
  delay?: number;
  y?: number;
  className?: string;
  once?: boolean;
}) {
  const reduced = useReducedMotion();
  return (
    <m.div
      className={className}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y, filter: "blur(8px)" }}
      whileInView={reduced ? { opacity: 1 } : { opacity: 1, y: 0, filter: "blur(0px)" }}
      viewport={{ once, margin: "0px 0px -64px 0px" }}
      transition={{ duration: reduced ? 0.5 : 0.7, ease: EASE_OUT, delay }}
    >
      {children}
    </m.div>
  );
}

/** Orchestrated group: parent controls timing, children declare `variants={fadeUp}`. */
export function Stagger({
  children,
  className,
  stagger = 0.08,
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  stagger?: number;
  delay?: number;
}) {
  return (
    <m.div
      className={className}
      variants={staggerContainer(stagger, delay)}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "0px 0px -64px 0px" }}
    >
      {children}
    </m.div>
  );
}

/** A staggered child — pairs with <Stagger>. */
export function Item({ children, className }: { children: ReactNode; className?: string }) {
  const variants = useFadeUp();
  return (
    <m.div className={className} variants={variants}>
      {children}
    </m.div>
  );
}

// ── display type entrance ─────────────────────────────────────────────────────

/** Headline reveal: words rise out of their own line-box one after another.
 *  Reserved for h1/h2 moments — used sparingly, it reads as typesetting. */
export function TextReveal({
  text,
  className,
  delay = 0,
  as: Tag = "span",
}: {
  text: string;
  className?: string;
  delay?: number;
  as?: "span" | "h1" | "h2" | "p";
}) {
  const words = text.split(" ");
  const reduced = useReducedMotion();

  // Reduced motion: no line-box rise — the whole headline simply fades in.
  // (Words at y:110% inside overflow-hidden boxes must never be the fallback.)
  if (reduced) {
    return (
      <Tag className={className}>
        <m.span
          className="inline"
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, ease: EASE_OUT, delay }}
        >
          {text}
        </m.span>
      </Tag>
    );
  }

  return (
    <Tag className={className}>
      <m.span
        className="inline"
        variants={staggerContainer(0.055, delay)}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true }}
      >
        {words.map((word, i) => (
          <span key={`${word}-${i}`} className="inline-block overflow-hidden align-bottom">
            <m.span
              className="inline-block"
              variants={{
                hidden: { y: "110%" },
                visible: { y: 0, transition: { duration: 0.65, ease: EASE_OUT } },
              }}
            >
              {word}
              {i < words.length - 1 ? " " : ""}
            </m.span>
          </span>
        ))}
      </m.span>
    </Tag>
  );
}

// ── numbers ───────────────────────────────────────────────────────────────────

/** Counts up when it enters the viewport. Respects reduced motion (jumps to the
 *  final value). Format with `suffix`/`prefix`, e.g. <CountUp to={92} suffix="%" />. */
export function CountUp({
  to,
  duration = 1.4,
  prefix = "",
  suffix = "",
  className,
  decimals = 0,
}: {
  to: number;
  duration?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
  decimals?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -40px 0px" });
  const reduced = useReducedMotion();
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!inView) return;
    if (reduced) {
      setValue(to);
      return;
    }
    const controls = animate(0, to, {
      duration,
      ease: EASE_OUT as unknown as [number, number, number, number],
      onUpdate: (v) => setValue(v),
    });
    return () => controls.stop();
  }, [inView, to, duration, reduced]);

  return (
    <span ref={ref} className={className}>
      {prefix}
      {value.toFixed(decimals)}
      {suffix}
    </span>
  );
}

/** A live integer that springs to its new value every time `value` changes — the reward
 *  for an action (checking a task ticks the counter up). Unlike <CountUp>, it isn't gated
 *  by entering the viewport; it re-tweens on each change. Reduced motion jumps instantly.
 *  Accessibility is handled internally: the tween is aria-hidden and the settled target
 *  value sits in a polite live region, so screen readers hear each new count once (never the
 *  intermediate frames) and always read the true value on navigation. */
export function Tally({ value, className }: { value: number; className?: string }) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current === value) return;
    if (reduced) {
      setShown(value);
      prev.current = value;
      return;
    }
    const controls = animate(prev.current, value, {
      duration: 0.5,
      ease: EASE_OUT as unknown as [number, number, number, number],
      onUpdate: (v) => setShown(v),
    });
    prev.current = value;
    return () => controls.stop();
  }, [value, reduced]);
  return (
    <span className={className}>
      <span aria-hidden="true">{Math.round(shown)}</span>
      <span className="sr-only" aria-live="polite">
        {value}
      </span>
    </span>
  );
}

// ── depth ─────────────────────────────────────────────────────────────────────

/** Scroll parallax: drifts children vertically as the section crosses the
 *  viewport. `range` is the total drift in px (positive = moves down slower). */
export function Parallax({
  children,
  range = 40,
  className,
}: {
  children: ReactNode;
  range?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start end", "end start"] });
  const y = useTransform(scrollYProgress, [0, 1], [range, -range]);
  return (
    <m.div ref={ref} style={{ y }} className={className}>
      {children}
    </m.div>
  );
}

/** Pointer-tracking 3D tilt for showcase surfaces. Springs keep it liquid; the
 *  whole effect disappears under reduced motion (MotionConfig zeroes transforms). */
export function Tilt({
  children,
  className,
  max = 7,
}: {
  children: ReactNode;
  className?: string;
  max?: number;
}) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const rotateX = useSpring(useTransform(y, [-0.5, 0.5], [max, -max]), { stiffness: 180, damping: 22 });
  const rotateY = useSpring(useTransform(x, [-0.5, 0.5], [-max, max]), { stiffness: 180, damping: 22 });

  return (
    <m.div
      className={className}
      style={{ rotateX, rotateY, transformStyle: "preserve-3d", perspective: 1000, willChange: "transform" }}
      onPointerMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        x.set((e.clientX - rect.left) / rect.width - 0.5);
        y.set((e.clientY - rect.top) / rect.height - 0.5);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      {children}
    </m.div>
  );
}

/** Magnetic pull on hover — for the one or two CTAs that deserve it. */
export function Magnetic({
  children,
  className,
  strength = 0.25,
}: {
  children: ReactNode;
  className?: string;
  strength?: number;
}) {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 220, damping: 18 });
  const sy = useSpring(y, { stiffness: 220, damping: 18 });
  return (
    <m.div
      className={className}
      style={{ x: sx, y: sy, display: "inline-block", willChange: "transform" }}
      onPointerMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        x.set((e.clientX - rect.left - rect.width / 2) * strength);
        y.set((e.clientY - rect.top - rect.height / 2) * strength);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      {children}
    </m.div>
  );
}
