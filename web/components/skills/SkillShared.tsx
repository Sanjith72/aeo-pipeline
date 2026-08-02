"use client";

// Shared five-skill presentation primitives (v5 CH-04/CH-06/CH-05), used by BOTH the free
// overview (OverviewView) and the gated pack detail (PackDetail). Extracted so the two
// surfaces cannot drift into showing the same score with different labels or a different
// idea of what "provisional" means — the honesty labelling is the point, and two copies of
// it would eventually disagree.
//
// Design system per CH-12: existing tokens only (.card, label-mono, ink/accent). Scores are
// length-encoded monochrome meters — no new palette; identity carried by text.

import { useState } from "react";
import { api } from "@/lib/api";
import type { SkillKey, SkillPriority } from "@/lib/types";

export const SKILL_META: { key: SkillKey; label: string; blurb: string }[] = [
  { key: "messaging", label: "Messaging", blurb: "Is it clear what you do, for whom, and why it matters?" },
  { key: "conversion", label: "Conversion", blurb: "Is there an obvious next step for a ready buyer?" },
  { key: "discovery_visibility", label: "Discovery & Visibility", blurb: "Can people — and AI — find and read this page?" },
  { key: "proof_trust", label: "Proof & Trust", blurb: "Do you show evidence a stranger would believe?" },
  { key: "structure_ux", label: "Structure & UX", blurb: "Does the page read in clean, scannable chunks?" },
];

// Honest provenance labels for SkillScore.confidence. "provisional" is the deterministic
// heuristic pass; "neutral" means we genuinely could not judge — never a fake 0.
export const CONFIDENCE_LABEL: Record<string, string> = {
  deterministic: "measured",
  hybrid: "AI-refined",
  provisional: "provisional",
  neutral: "not yet judged",
};

export function skillLabel(key: string): string {
  return SKILL_META.find((s) => s.key === key)?.label ?? key;
}

/** Length-encoded 0-100 meter in house monochrome (track white/10, fill accent). */
export function Meter({ value }: { value: number }) {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
      role="img"
      aria-label={`Score ${value} out of 100`}
    >
      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

// v5 CH-05 ("let the AI decide, humans validate") — the ranked fixes are an AI decision, so
// each one is dismissible and the dismissal is CAPTURED as a learning signal
// (POST /api/overrides -> events + a human-gated 'proposed' refinement, never auto-applied).
// Local-only state: dismissing hides the row for this reader; it never edits the stored
// ranking, so one person's judgement can't silently rewrite another's report.
export function PriorityList({
  priorities,
  context,
  compact = false,
}: {
  priorities: SkillPriority[];
  /** Domain or page URL the override is about — the learning signal is useless without it. */
  context: string;
  /** Denser rows for the per-page pack view, where many lists appear on one screen. */
  compact?: boolean;
}) {
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  function dismiss(index: number, p: SkillPriority) {
    setDismissed((prev) => new Set(prev).add(index));
    api.captureOverride(
      `priority:${p.skill}:${p.criterion ?? "llm"}`,
      p.text,
      null,
      "recommendation_rejected",
      context,
    );
  }

  const visible = priorities.map((p, i) => ({ p, i })).filter(({ i }) => !dismissed.has(i));

  return (
    <>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {visible.map(({ p, i }, position) => (
          <li
            key={`${p.skill}-${i}`}
            className={compact ? "flex items-start gap-3" : "card flex items-start gap-3 p-4"}
          >
            <span className="font-display text-[15px] font-semibold text-accent">
              {String(position + 1).padStart(2, "0")}
            </span>
            <div className="min-w-0 flex-1">
              <p className="m-0 text-[14px] leading-[1.5] text-ink">{p.text}</p>
              <span className="label-mono !text-[10px] text-ink-300">
                {skillLabel(p.skill)}
                {/* CH-06: say when the lift factor was imputed rather than measured, so a
                    reader can tell a grounded rank from a substituted one. */}
                {p.lift_basis === "imputed" && " · est. impact"}
              </span>
            </div>
            <button
              type="button"
              onClick={() => dismiss(i, p)}
              // >=40px target per DESIGN.md's accessibility rules.
              className="-m-2 min-h-[40px] min-w-[40px] shrink-0 rounded-lg p-2 text-[12px] text-ink-300 transition hover:text-ink"
              aria-label={`Not relevant: ${p.text}`}
              title="Not relevant to my business"
            >
              Not relevant
            </button>
          </li>
        ))}
      </ol>
      {dismissed.size > 0 && (
        <p className="mt-3 text-[12.5px] text-ink-300" role="status">
          {dismissed.size} fix{dismissed.size === 1 ? "" : "es"} hidden. Thanks — we use this to
          rank better.{" "}
          <button
            type="button"
            onClick={() => setDismissed(new Set())}
            className="underline underline-offset-2 hover:text-ink"
          >
            Undo
          </button>
        </p>
      )}
    </>
  );
}
