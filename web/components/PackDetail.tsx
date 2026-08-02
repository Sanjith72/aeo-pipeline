"use client";

// v5 CH-04/CH-02a — the gated per-page five-skill detail for one unlocked pack.
//
// This renders GET /api/packs/{run_id}/{pack_index}, which had no consumer at all: the
// route, its server-side 403, its types and its tests all shipped, but nothing displayed
// the payload. The ticket board next to this shows WHAT TO DO; this shows WHY — the five
// skill scores behind those tickets, page by page, with the impact-ranked fixes (CH-06).
//
// The 403 is the same gate as the ticket board's: a locked pack renders a lock message
// rather than an error, because being locked is an expected state, not a failure.

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PackPageDetail, SkillKey, SkillScore } from "@/lib/types";
import {
  CONFIDENCE_LABEL,
  Meter,
  PriorityList,
  SKILL_META,
} from "@/components/skills/SkillShared";

/** "/pricing" from a full URL — the whole origin repeats on every row and adds no signal. */
function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname === "/" ? u.hostname : u.pathname;
  } catch {
    return url;
  }
}

// CH-14: the AI-snapshot rides on the Discovery skill. Honest about all three states — an
// 'unavailable' check is shown as not-measured, never dressed up as a pass or a failure.
function AiVisibilityChip({ skill }: { skill: SkillScore }) {
  const v = skill.ai_visibility;
  if (!v) return null;
  if (v.status === "cited") {
    return (
      <span className="label-mono !text-[10px] text-emerald-300">
        ✓ cited by AI{v.via === "answer_text" ? " (mentioned)" : ""}
      </span>
    );
  }
  if (v.status === "not_cited") {
    return <span className="label-mono !text-[10px] text-ink-300">not cited by AI yet</span>;
  }
  return <span className="label-mono !text-[10px] text-ink-300">AI check not run</span>;
}

function SkillRow({ label, blurb, skill }: { label: string; blurb: string; skill: SkillScore }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13.5px] font-medium text-ink">{label}</span>
        <span className="font-display text-[15px] font-semibold leading-none text-accent">
          {skill.score}
        </span>
      </div>
      <Meter value={skill.score} />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="label-mono !text-[10px] text-ink-300">
          {CONFIDENCE_LABEL[skill.confidence] ?? skill.confidence}
        </span>
        <AiVisibilityChip skill={skill} />
      </div>
      <p className="m-0 text-[12.5px] leading-[1.5] text-ink-300">{blurb}</p>
      {skill.suggestions.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {skill.suggestions.slice(0, 2).map((s) => (
            <li key={s.id} className="flex gap-2 text-[13px] leading-[1.5] text-ink-500">
              <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-white/40" />
              {s.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PageCard({ page }: { page: PackPageDetail }) {
  const detail = page.detail;
  return (
    <article className="card flex flex-col gap-4 p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <h5 className="m-0 truncate text-[14.5px] font-semibold text-ink" title={page.url}>
            {pathOf(page.url)}
          </h5>
          <span className="label-mono !text-[10px] text-ink-300">{page.page_type}</span>
        </div>
        {page.overall != null && (
          <div className="flex items-baseline gap-1.5">
            <span className="font-display text-[24px] font-semibold leading-none text-accent">
              {page.overall}
            </span>
            <span className="label-mono !text-[10px] text-ink-300">overall</span>
          </div>
        )}
      </header>

      {detail == null ? (
        // Honest empty state: a page can be in a pack without a scored skill payload (e.g.
        // it was unreachable on the crawl). Say so rather than rendering five zeros.
        <p className="m-0 text-[13px] text-ink-300">
          No skill scores for this page yet — it may not have been readable on the last crawl.
        </p>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            {SKILL_META.map((m) => {
              const s = detail.skills[m.key as SkillKey];
              return s ? <SkillRow key={m.key} label={m.label} blurb={m.blurb} skill={s} /> : null;
            })}
          </div>
          {detail.priorities.length > 0 && (
            <div className="border-t border-white/[0.09] pt-4">
              <p className="label-mono mb-2 !text-[10px] text-ink-300">Fix these first</p>
              {/* CH-05: overrides are captured against the PAGE url, not the domain — a
                  rejected fix is only a useful signal if we know which page it was for. */}
              <PriorityList priorities={detail.priorities} context={page.url} compact />
            </div>
          )}
        </>
      )}
    </article>
  );
}

export function PackDetail({ runId, packIndex }: { runId: number; packIndex: number }) {
  const [pages, setPages] = useState<PackPageDetail[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLocked(false);
    setError(false);
    try {
      setPages((await api.getPackDetail(runId, packIndex)).pages);
    } catch (e) {
      // A locked pack is an expected state (CH-02a), not a failure.
      if (typeof e === "object" && e !== null && (e as { status?: number }).status === 403) {
        setLocked(true);
        return;
      }
      setError(true);
    }
  }, [runId, packIndex]);

  useEffect(() => {
    void load();
  }, [load]);

  if (locked)
    return <p className="text-[13px] text-ink-300">Unlock this pack to see its page-by-page scores.</p>;
  if (error) return <p className="text-[13px] text-ink-300">Couldn&apos;t load the pack detail.</p>;
  if (pages == null) return <p className="text-[13px] text-ink-300">Loading page scores…</p>;
  if (pages.length === 0)
    return <p className="text-[13px] text-ink-300">No scored pages in this pack yet.</p>;

  return (
    <div className="flex flex-col gap-4">
      {pages.map((p) => (
        <PageCard key={p.url} page={p} />
      ))}
    </div>
  );
}
