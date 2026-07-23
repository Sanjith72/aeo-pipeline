"use client";

// Shared pack card (v5 CH-03) — renders one PackPreview identically in the free overview
// preview and the persisted deep-audit pack list. Design system per CH-12: existing
// tokens (.card, ink/accent, label-mono); the locked state dims + labels, never a new color.
// In the gated context (studio results, CH-02a) a locked pack shows an "Unlock" CTA.

import type { PackPreview } from "@/lib/types";

function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname === "/" ? u.hostname : u.pathname;
  } catch {
    return url;
  }
}

export function PackCard({
  pack,
  ctaMode = "preview",
  onUnlock,
  onOpen,
  opened = false,
}: {
  pack: PackPreview;
  // "preview" (overview/public — never asks for auth, per CH-11b) vs "gated" (studio
  // results — a locked pack invites unlock).
  ctaMode?: "preview" | "gated";
  onUnlock?: () => void;
  // Present on an UNLOCKED pack in the gated context — opens its ticket board (CH-08).
  onOpen?: () => void;
  opened?: boolean;
}) {
  return (
    <div className={`card flex h-full flex-col gap-3 p-5 ${pack.locked ? "opacity-80" : ""}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold text-ink">
          <span className="label-mono mr-2 !text-[10px] text-ink-300">
            Pack {String(pack.pack_index).padStart(2, "0")}
          </span>
          {pack.title}
        </h3>
        {pack.locked && <span className="label-mono !text-[10px] text-ink-300">Locked</span>}
      </div>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {pack.pages.map((p) => (
          <li key={p.url} className="truncate font-mono text-[12px] text-ink-500" title={p.url}>
            {pathOf(p.url)}
          </li>
        ))}
      </ul>
      {pack.locked ? (
        ctaMode === "gated" && onUnlock ? (
          <button type="button" onClick={onUnlock} className="btn-ghost mt-auto self-start text-[12.5px]">
            Unlock this pack →
          </button>
        ) : (
          <p className="m-0 mt-auto text-[12.5px] text-ink-300">Unlocks after your homepage pack.</p>
        )
      ) : (
        onOpen && (
          <button type="button" onClick={onOpen} className="btn-ghost mt-auto self-start text-[12.5px]">
            {opened ? "Hide fixes" : "Open fixes →"}
          </button>
        )
      )}
    </div>
  );
}
