"use client";

// Custom select — button + popover listbox following the WAI-ARIA listbox pattern.
// Keyboard: ArrowUp/Down, Home/End, Enter/Space, Escape, and type-ahead. Animated
// open (menu-pop), closes on outside pointerdown, scrolls the active row into view.

import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "./icons";

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  value,
  onChange,
  options,
  placeholder = "Select…",
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(() => Math.max(0, options.findIndex((o) => o.value === value)));
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const typeahead = useRef({ buffer: "", at: 0 });
  const listboxId = useId();

  const selected = options.find((o) => o.value === value) ?? null;

  useEffect(() => {
    if (!open) return;
    function onOutside(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onOutside);
    return () => document.removeEventListener("pointerdown", onOutside);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector(`[data-index="${active}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  function show() {
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  }

  function pick(index: number) {
    const opt = options[index];
    if (opt) onChange(opt.value);
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      show();
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(options.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      pick(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "Tab") {
      setOpen(false);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // type-ahead: jump to the first option starting with the typed prefix
      const now = Date.now();
      const t = typeahead.current;
      t.buffer = (now - t.at < 600 ? t.buffer : "") + e.key.toLowerCase();
      t.at = now;
      const hit = options.findIndex((o) => o.label.toLowerCase().startsWith(t.buffer));
      if (hit >= 0) setActive(hit);
    }
  }

  return (
    <div ref={rootRef} className="relative" onKeyDown={onKeyDown}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        onClick={() => (open ? setOpen(false) : show())}
        className="input flex items-center justify-between gap-2 text-left"
      >
        <span className={selected ? "" : "text-ink-300"}>{selected?.label ?? placeholder}</span>
        <ChevronDown className={`shrink-0 text-ink-300 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listboxId}
          role="listbox"
          aria-activedescendant={`${listboxId}-${active}`}
          className="menu-pop absolute z-30 mt-1.5 max-h-64 w-full overflow-auto overscroll-contain rounded-xl border border-ink/10 bg-paper-100 p-1.5 shadow-lift"
        >
          {options.map((opt, i) => {
            const isSel = opt.value === value;
            return (
              <li
                key={opt.value}
                id={`${listboxId}-${i}`}
                data-index={i}
                role="option"
                aria-selected={isSel}
                onPointerMove={() => setActive(i)}
                onClick={() => pick(i)}
                className={`flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-colors ${
                  i === active ? "bg-ink/[0.05] text-ink" : "text-ink-500"
                }`}
              >
                {opt.label}
                {isSel && <Check className="animate-pop text-accent" />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
