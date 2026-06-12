"use client";

// The one scroll hook framer-motion doesn't cover: a boolean "page has scrolled"
// flag for the navbar morph (everything continuous lives in motion/primitives).

import { useEffect, useState } from "react";

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
