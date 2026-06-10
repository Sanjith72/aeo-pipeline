"use client";

// Two tiny motion hooks — all the JS this site's animation system needs.
// Everything else is GPU-composited CSS (see globals.css / tailwind.config.ts).

import { useEffect, useRef, useState } from "react";

/** True once the page is scrolled past `threshold` px — drives the navbar morph.
 *  Passive listener + rAF coalescing: never blocks scrolling, max one state
 *  check per frame. */
export function useScrolled(threshold = 12): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    let ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        setScrolled(window.scrollY > threshold);
        ticking = false;
      });
    }
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

/** 0..1 page scroll progress — drives the hairline progress bar under the navbar.
 *  Same passive + rAF pattern as useScrolled. */
export function useScrollProgress(): number {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    let ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const el = document.documentElement;
        const max = el.scrollHeight - el.clientHeight;
        setProgress(max > 0 ? Math.min(1, el.scrollTop / max) : 0);
        ticking = false;
      });
    }
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  return progress;
}

/** Counts 0 → target once after `delay` ms (eased), then stays put. Renders the
 *  final value immediately for users who prefer reduced motion. */
export function useCountUp(target: number, { duration = 900, delay = 350 } = {}): number {
  const [value, setValue] = useState(0);
  const frame = useRef(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return;
    }
    let start = 0;
    const timer = window.setTimeout(() => {
      const step = (now: number) => {
        if (!start) start = now;
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
        setValue(Math.round(eased * target));
        if (t < 1) frame.current = requestAnimationFrame(step);
      };
      frame.current = requestAnimationFrame(step);
    }, delay);
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(frame.current);
    };
  }, [target, duration, delay]);
  return value;
}
