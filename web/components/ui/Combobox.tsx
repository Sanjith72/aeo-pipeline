"use client";

// Searchable combobox — a text input with a filtered suggestion list (WAI-ARIA
// combobox pattern). Free text is always allowed: suggestions assist, never gate.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown } from "./icons";

function Highlight({ text, query }: { text: string; query: string }) {
  const q = query.trim().toLowerCase();
  if (!q) return <>{text}</>;
  const at = text.toLowerCase().indexOf(q);
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <span className="font-semibold text-ink">{text.slice(at, at + q.length)}</span>
      {text.slice(at + q.length)}
    </>
  );
}

export function Combobox({
  value,
  onChange,
  options,
  placeholder,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  options: readonly string[];
  placeholder?: string;
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listboxId = useId();

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase();
    if (!q) return [...options];
    const hits = options.filter((o) => o.toLowerCase().includes(q));
    // exact text already chosen → show the full list again so it's easy to switch
    return hits.length === 1 && hits[0].toLowerCase() === q ? [...options] : hits;
  }, [value, options]);

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

  function pick(index: number) {
    const opt = filtered[index];
    if (opt !== undefined) onChange(opt);
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      if (open && filtered.length > 0) {
        e.preventDefault();
        pick(active);
      }
    } else if (e.key === "Escape" || e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <input
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-activedescendant={open && filtered.length > 0 ? `${listboxId}-${active}` : undefined}
        aria-autocomplete="list"
        aria-label={ariaLabel}
        className="input pr-9"
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        tabIndex={-1}
        aria-hidden
        onClick={() => setOpen((o) => !o)}
        className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-ink-300"
      >
        <ChevronDown className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && filtered.length > 0 && (
        <ul
          ref={listRef}
          id={listboxId}
          role="listbox"
          className="menu-pop glass absolute z-30 mt-1.5 max-h-64 w-full overflow-auto overscroll-contain rounded-xl p-1.5 shadow-lift"
        >
          {filtered.map((opt, i) => {
            const isSel = opt === value;
            return (
              <li
                key={opt}
                id={`${listboxId}-${i}`}
                data-index={i}
                role="option"
                aria-selected={isSel}
                onPointerMove={() => setActive(i)}
                // pointerdown (not click) so the pick lands before the input blurs
                onPointerDown={(e) => {
                  e.preventDefault();
                  pick(i);
                }}
                className={`flex cursor-pointer items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-colors ${
                  i === active ? "bg-ink/[0.05] text-ink" : "text-ink-500"
                }`}
              >
                <span>
                  <Highlight text={opt} query={value} />
                </span>
                {isSel && <Check className="animate-pop text-accent" />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
