"use client";

// The unified "Show me how" expander — ONE component rendering the combined, de-duplicated
// superset of what the two old expanders showed (results.tsx TaskCard + MilestoneDashboard
// TaskGuide). Imported by BOTH the no-domain/resume PlanTask path and the milestone path,
// each of which maps its own task shape onto the normalized prop below.
//
// Layout (combined by ROLE — never two how-tos stacked):
//   1. Context block: "Where you are now" / "What to do" / "How to do it" (each omitted
//      when its field is absent).
//   2. Two-tab panel (B's tab UX verbatim):
//      • "I'll do it myself": numbered diySteps → else the prompts.human checklist → else
//        actionRequired; plus the rawSnippet code block when present; plus the prompts.ai
//        "Doing it with AI" box as a clearly separate OPTIONAL shortcut (never a second
//        how-to next to the steps — and suppressed-as-steps once diySteps exist).
//      • "Send to Developer": dev_brief (→ actionRequired) + read-only tracking link + mailto.
// Every sub-block degrades gracefully: absent field → omitted, never empty. rawSnippet has
// NO fallback (no invented placeholder code).

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  clearWindowSelection,
  manualCopyHint,
  selectBlock,
  selectField,
  useCopyAction,
} from "@/lib/copy";

// The normalized, source-agnostic shape both call sites map onto. PlanTask supplies `id`
// for taskKey + prompts/current_state; MilestoneTask supplies `task_key` + diy/dev fields.
export interface TaskHowToProps {
  taskKey: string;
  label: string;
  currentState?: string;
  actionRequired: string;
  howTo?: string;
  prompts?: { ai: string; human: string };
  diySteps?: string[];
  rawSnippet?: string | null;
  devBrief?: string;
  shareUrl?: string | null;
  /** False hides the "Send to Developer" tab (the gamified Roadmap keeps only the DIY
   *  guide — developer handoff lives on the Strategy tab as its own section). */
  showDeveloper?: boolean;
}

type GuideTab = "diy" | "developer";

export function TaskHowTo({
  taskKey,
  label,
  currentState,
  actionRequired,
  howTo,
  prompts,
  diySteps,
  rawSnippet,
  devBrief,
  shareUrl = null,
  showDeveloper = true,
}: TaskHowToProps) {
  const [tab, setTab] = useState<GuideTab>("diy");
  const activeTab: GuideTab = showDeveloper ? tab : "diy";
  return (
    <div className="step-in mt-3 space-y-3">
      {/* 1. Context block — at least "What to do" always shows; the others when present. */}
      <div className="space-y-2.5 rounded-lg border border-ink/[0.06] bg-paper-200/40 px-3.5 py-3 text-sm">
        {currentState && <Detail label="Where you are now" value={currentState} />}
        <Detail label="What to do" value={actionRequired} />
        {howTo && <Detail label="How to do it" value={howTo} />}
      </div>

      {/* 2. Two-tab panel — B's tab UX verbatim. The tablist collapses away when the
          developer tab is suppressed (only one option left = no choice to present). */}
      {showDeveloper && (
        <div
          role="tablist"
          aria-label="Implementation options"
          className="flex gap-1 rounded-lg border border-ink/10 bg-paper-200/60 p-1 text-[12px]"
        >
          {(
            [
              { id: "diy" as const, label: "I'll do it myself" },
              { id: "developer" as const, label: "Send to Developer" },
            ]
          ).map(({ id, label: tabLabel }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              className={`flex-1 rounded-md px-3 py-1.5 transition-all duration-200 ${
                tab === id ? "bg-paper-100 font-medium text-ink shadow-card" : "text-ink-300 hover:text-ink-500"
              }`}
            >
              {tabLabel}
            </button>
          ))}
        </div>
      )}
      {activeTab === "diy" ? (
        <DiyGuide
          taskKey={taskKey}
          actionRequired={actionRequired}
          diySteps={diySteps}
          rawSnippet={rawSnippet}
          promptHuman={prompts?.human}
          promptAi={prompts?.ai}
        />
      ) : (
        <DeveloperHandoff
          taskKey={taskKey}
          label={label}
          devBrief={devBrief}
          actionRequired={actionRequired}
          shareUrl={shareUrl}
        />
      )}
    </div>
  );
}

// A label/value row (single source of truth for the label-over-value pattern).
export function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="label-mono">{label}</span>
      <p className="mt-0.5 leading-relaxed text-ink-500">{value}</p>
    </div>
  );
}

// "I'll do it myself" — the steps (with de-dup guard), the optional paste-able snippet, and
// the optional "Doing it with AI" shortcut.
//
// De-dup guard (the whole reason prompts now flows to milestone tasks): numbered diy_steps
// WIN. The prompts.human checklist is only the fallback WHEN there are no diy_steps — so the
// two never read as duplicate how-tos. The AI prompt is rendered below, divided off, framed
// as an optional shortcut — not as a second set of steps beside the first.
function DiyGuide({
  taskKey,
  actionRequired,
  diySteps,
  rawSnippet,
  promptHuman,
  promptAi,
}: {
  taskKey: string;
  actionRequired: string;
  diySteps?: string[];
  rawSnippet?: string | null;
  promptHuman?: string;
  promptAi?: string;
}) {
  const snippet = rawSnippet ?? null;
  const codeRef = useRef<HTMLElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => snippet,
    selectFallback: () => selectBlock(codeRef.current),
    clearSelection: clearWindowSelection,
    copiedMs: 2000,
    onCopied: () => api.track("diy_snippet_copied", { task_key: taskKey }),
  });

  const hasSteps = !!diySteps && diySteps.length > 0;

  return (
    <div className="rounded-lg border border-ink/[0.08] bg-paper-200/30 px-3.5 py-3 text-sm">
      {hasSteps ? (
        <ol className="ml-4 list-decimal space-y-1.5 leading-relaxed text-ink-500 marker:text-ink-300">
          {diySteps!.map((step, i) => (
            <li key={i} className="pl-1">
              {step}
            </li>
          ))}
        </ol>
      ) : promptHuman ? (
        // Fallback when there are no CMS steps (the no-domain plan path): the human recipe.
        <p className="whitespace-pre-wrap leading-relaxed text-ink-500">{promptHuman}</p>
      ) : (
        <p className="text-ink-500">{actionRequired}</p>
      )}

      {snippet && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className="label-mono">Code snippet</span>
            <button
              type="button"
              onClick={copy}
              className="btn-primary !py-1 text-[11px]"
              aria-label="Copy code snippet to clipboard"
            >
              {phase === "copied" ? "Copied!" : phase === "manual" ? manualCopyHint() : "Copy Code"}
            </button>
          </div>
          <pre className="max-h-72 w-full overflow-auto rounded-md border border-ink/10 bg-paper p-3 font-mono text-[11px] leading-relaxed text-emerald-200 shadow-card">
            <code ref={codeRef}>{snippet}</code>
          </pre>
        </div>
      )}

      {/* Optional AI shortcut — separated from the steps so it never reads as a 2nd how-to. */}
      {promptAi && (
        <div className="mt-3 border-t border-ink/[0.08] pt-3">
          <PromptBox
            title="Shortcut: do it with AI"
            blurb="Optional — paste this into ChatGPT or your builder's AI and use what it writes."
            text={promptAi}
            copyLabel="Copy prompt"
          />
        </div>
      )}
    </div>
  );
}

// Developer Handoff — a paste-ready message (greeting + technical brief + the read-only
// tracking link when available) the owner hands to their developer, with one-click copy and
// a pre-filled mailto. Degrades when shareUrl is null: brief + mailto still work, just no
// tracking link, and the null-state copy stays generic (no milestone-specific promise).
function DeveloperHandoff({
  taskKey,
  label,
  devBrief,
  actionRequired,
  shareUrl,
}: {
  taskKey: string;
  label: string;
  devBrief?: string;
  actionRequired: string;
  shareUrl: string | null;
}) {
  const brief = devBrief?.trim() || actionRequired;
  const message = buildHandoffMessage(brief, shareUrl);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { phase, copy } = useCopyAction({
    getText: () => message,
    selectFallback: () => selectField(textareaRef.current),
    clearSelection: () => textareaRef.current?.setSelectionRange(0, 0),
    onCopied: () => api.track("dev_handoff_copied", { task_key: taskKey }),
  });

  const mailto =
    `mailto:?subject=${encodeURIComponent(`Website update request: ${label}`)}` +
    `&body=${encodeURIComponent(message)}`;

  return (
    <div className="rounded-lg border border-accent/30 bg-accent/[0.04] px-3.5 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="label-mono !text-accent">Developer handoff</span>
        {shareUrl && (
          <span className="font-mono text-[11px] text-ink-300">read-only tracking link included</span>
        )}
      </div>
      <p className="mt-1 text-xs text-ink-500">
        Hand this to your web developer — it has exact, paste-ready instructions
        {shareUrl ? " and a live link to track progress." : "."}
      </p>
      <textarea
        ref={textareaRef}
        readOnly
        value={message}
        rows={9}
        onFocus={(e) => e.currentTarget.select()}
        className="mt-2 w-full resize-y rounded-md border border-ink/10 bg-paper p-2.5 font-mono text-[11px] leading-relaxed text-ink-700"
        aria-label={`Developer brief for ${label}`}
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={copy} className="btn-primary !py-1.5 text-[12px]">
          {phase === "copied" ? "Copied ✓" : phase === "manual" ? manualCopyHint() : "Copy brief & link"}
        </button>
        <a
          href={mailto}
          onClick={() => api.track("dev_handoff_emailed", { task_key: taskKey })}
          className="btn-ghost !py-1.5 text-[12px]"
        >
          Send via email
        </a>
      </div>
      {!shareUrl && (
        <p className="mt-2 text-[11px] text-ink-300">
          The brief above is ready to copy or send by email as-is.
        </p>
      )}
    </div>
  );
}

function buildHandoffMessage(brief: string, shareUrl: string | null): string {
  const lines = [
    "Hi,",
    "",
    "Could you make the following change to our website? The technical details are below.",
    "",
    brief,
  ];
  if (shareUrl) {
    lines.push(
      "",
      "You can see this task (and our full implementation plan) on this live, read-only tracker — it updates automatically as changes go live:",
      shareUrl,
    );
  }
  lines.push("", "Thanks!");
  return lines.join("\n");
}

// The "Doing it with AI" copy box (relocated from results.tsx). A titled prompt with a
// raw-clipboard copy button — preserved verbatim (no analytics; results.tsx never tracked it).
function PromptBox({ title, blurb, text, copyLabel }: { title: string; blurb: string; text: string; copyLabel: string }) {
  return (
    <div className="rounded-lg border border-ink/[0.08] bg-paper-100 p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-ink">{title}</span>
        <CopyButton text={text} label={copyLabel} />
      </div>
      <p className="mb-2 text-[11px] text-ink-300">{blurb}</p>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-ink/[0.03] p-2 font-mono text-[11px] leading-relaxed text-ink-500">
        {text}
      </pre>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          },
          () => {},
        );
      }}
      className="btn-ghost !px-2.5 !py-1 text-[11px]"
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}
