// Clipboard copy with a graceful fallback — shared by every copy button in the app
// (the developer share link, the DIY code snippet, the developer handoff brief).
//
// navigator.clipboard.writeText rejects in restricted contexts (no transient user
// activation, a sandboxed iframe, a tight permissions-policy). Failing silently leaves the
// user clicking a dead button, so on rejection we instead SELECT the source text and prompt
// a keyboard copy. Lives in lib/ so both MilestoneDashboard (ManageAccess) and the shared
// TaskHowTo expander import the one implementation.

import { useCallback, useEffect, useRef, useState } from "react";

export type CopyPhase = "idle" | "copied" | "manual";

// The keyboard hint, OS-aware. Only ever rendered after a click (the "manual" phase), so
// reading `navigator` here can't cause an SSR/hydration mismatch on first paint.
export function manualCopyHint(): string {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.platform);
  return mac ? "Press ⌘+C to copy" : "Press Ctrl+C to copy";
}

// Highlight a text field (input/textarea) so a manual Ctrl/⌘+C grabs its full value.
export function selectField(el: HTMLInputElement | HTMLTextAreaElement | null) {
  if (!el) return;
  el.focus();
  el.select();
}

// Highlight the contents of a non-editable block (our <pre><code> snippet) via a Range.
export function selectBlock(el: HTMLElement | null) {
  if (!el || typeof window === "undefined") return;
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

export function clearWindowSelection() {
  if (typeof window !== "undefined") window.getSelection()?.removeAllRanges();
}

export function useCopyAction({
  getText,
  selectFallback,
  clearSelection,
  copiedMs = 1500,
  onCopied,
}: {
  getText: () => string | null;
  selectFallback: () => void;
  clearSelection: () => void;
  copiedMs?: number;
  onCopied?: () => void;
}): { phase: CopyPhase; copy: () => void } {
  const [phase, setPhase] = useState<CopyPhase>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = useCallback(() => {
    const text = getText();
    if (text == null) return;
    if (timer.current) clearTimeout(timer.current);
    const revert = (ms: number, after?: () => void) => {
      timer.current = setTimeout(() => {
        setPhase("idle");
        after?.();
      }, ms);
    };
    const succeed = () => {
      setPhase("copied");
      onCopied?.();
      revert(copiedMs);
    };
    // No Clipboard API, or it rejected → select the text and ask for a keyboard copy,
    // clearing the selection when the prompt reverts after 2s.
    const fallback = () => {
      selectFallback();
      setPhase("manual");
      revert(2000, clearSelection);
    };
    try {
      const result = navigator.clipboard?.writeText(text);
      if (result && typeof result.then === "function") result.then(succeed, fallback);
      else fallback();
    } catch {
      fallback();
    }
  }, [getText, selectFallback, clearSelection, copiedMs, onCopied]);

  return { phase, copy };
}
