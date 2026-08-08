"use client";

// v5 / Phase 3 item 3.5 — page-by-page scores as their own tab.
//
// PackDetail used to dump EVERY page card, all five skills and every priority inline under
// the pack grid. On a real audit that is a wall: five meters and a fix list per page, times
// every page in the pack, with no way to look at one page. That is the friction this splits
// up — a list on the left, one page's detail on the right, and that page's fixes folded into
// an accordion that starts CLOSED so the first thing you see is the scores, not the work.
//
// Two things it must not break, both carried over from PackDetail:
//   * a locked pack renders a lock message, never an error — being locked is an expected
//     product state (CH-02a), and the server 403s rather than shipping a locked pack's data;
//   * a page with no skill payload says so, rather than rendering five zeros, which would
//     read as "we scored this and it got nothing".
//
// Mobile: no two-column layout below 640px. The list becomes a <select> page picker, because
// a squeezed sidebar next to a squeezed detail pane makes both unusable.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { groupFixesByPage, packFixDomId, pageFixCounts } from "@/lib/packPlan";
import type { PackPageDetail, PackPreview, SkillKey, SkillScore, Ticket } from "@/lib/types";
import { CONFIDENCE_LABEL, Meter, PriorityList, SKILL_META } from "@/components/skills/SkillShared";
import { TaskHowTo } from "@/components/TaskHowTo";
import { Check } from "@/components/ui/icons";

/** "/pricing" from a full URL — the origin repeats on every row and adds no signal. */
function pathOf(url: string): string {
  try {
    const u = new URL(url);
    return u.pathname === "/" ? u.hostname : u.pathname;
  } catch {
    return url;
  }
}

/** Score → tone, matching the band language the rest of the app uses. */
function scoreTone(score: number | null): { dot: string; text: string } {
  if (score == null) return { dot: "bg-ink/20", text: "text-ink-300" };
  if (score >= 70) return { dot: "bg-emerald-400", text: "text-emerald-300" };
  if (score >= 40) return { dot: "bg-amber-400", text: "text-amber-200" };
  return { dot: "bg-rose-400", text: "text-rose-300" };
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
        <span className="font-display text-[15px] font-semibold leading-none text-accent">{skill.score}</span>
      </div>
      <Meter value={skill.score} />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="label-mono !text-[10px] text-ink-300">
          {CONFIDENCE_LABEL[skill.confidence] ?? skill.confidence}
        </span>
        <AiVisibilityChip skill={skill} />
      </div>
      <p className="m-0 text-[12.5px] leading-[1.5] text-ink-300">{blurb}</p>
    </div>
  );
}

/** How often to re-read tickets while any of them is awaiting its verification crawl. */
const VERIFY_POLL_MS = 5000;

export function PagesPanel({
  runId,
  packs,
  selectedPack,
  onSelectPack,
  onUnlock,
  shareUrl,
}: {
  runId: number;
  packs: PackPreview[];
  selectedPack: number | null;
  onSelectPack: (packIndex: number) => void;
  onUnlock: (packIndex: number) => void;
  /** The tracker's share link, so a fix's dev handoff matches the plan's. Lifted out of
   *  TrackerView rather than obtained by mounting a second useQuestTracker — that would fire
   *  a second POST /api/milestones sync, which TrackerView's header comment forbids. */
  shareUrl: string | null;
}) {
  const [pages, setPages] = useState<PackPageDetail[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState(false);
  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  // The fixes themselves. A SEPARATE loader from `load` on purpose: `load` resets `pages`,
  // `error` AND `selectedUrl`, so reusing it after "Mark as done" would throw the user back
  // to the first page in the sidebar every time they completed something.
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [fixError, setFixError] = useState<string | null>(null);
  const [lockedFixCount, setLockedFixCount] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    if (selectedPack == null) return;
    setLocked(false);
    setError(false);
    setPages(null);
    try {
      const res = await api.getPackDetail(runId, selectedPack);
      setPages(res.pages);
      // Land on the first page so the panel is never an empty right-hand column.
      setSelectedUrl(res.pages[0]?.url ?? null);
    } catch (e) {
      if ((e as { status?: number })?.status === 403) {
        setLocked(true);
        setPages([]);
        return;
      }
      setError(true);
    }
  }, [runId, selectedPack]);

  const loadTickets = useCallback(async () => {
    if (selectedPack == null) return;
    try {
      const res = await api.getPackTickets(runId, selectedPack);
      setTickets(res.tickets);
      setFixError(null);
    } catch (e) {
      // A locked pack is a 403 by design — a product state, not a failure. It must NOT set
      // `error`, which renders "Couldn't load these page scores" and reads as a bug.
      if ((e as { status?: number })?.status === 403) {
        setLocked(true);
        setTickets([]);
        return;
      }
      setFixError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, selectedPack]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setTickets(null);
    void loadTickets();
  }, [loadTickets]);

  // "N more fixes in locked packs" — the count the server computes for exactly this sentence.
  // Keyed on the LOCKED SET, not just runId: unlocking a pack must drop the count. `null`
  // means we don't know (the probe failed), which renders as nothing — never as "0 more".
  const lockedKey = packs.filter((p) => p.locked).map((p) => p.pack_index).join(",");
  useEffect(() => {
    let cancelled = false;
    api
      .getTickets(runId)
      .then((res) => {
        if (!cancelled) setLockedFixCount(res.locked_ticket_count ?? 0);
      })
      .catch(() => {
        if (!cancelled) setLockedFixCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, lockedKey]);

  // Poll only while something is actually awaiting its crawl, and PACK-WIDE — filtering this
  // to the selected page would stop polling a fix the moment the user looked at another page.
  const anyVerifying = tickets?.some((t) => t.status === "closed_pending_verify") ?? false;
  useEffect(() => {
    if (!anyVerifying) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = setInterval(() => void loadTickets(), VERIFY_POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [anyVerifying, loadTickets]);

  const act = async (taskKey: string, fn: () => Promise<unknown>) => {
    setBusyKey(taskKey);
    setFixError(null);
    try {
      await fn();
      await loadTickets(); // NOT load() — that would reset the selected page
    } catch (e) {
      setFixError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyKey(null);
    }
  };

  // Land on a pack automatically when none is chosen yet. Without this the tab is a dead
  // end: the selector is hidden when there is only ONE pack (a single chip that cannot be
  // switched is noise), so "Pick a pack" would be an instruction with nothing to click.
  // Prefer an unlocked pack — opening on a locked one shows the paywall before the user has
  // seen anything they already own.
  useEffect(() => {
    if (selectedPack != null || packs.length === 0) return;
    const target = packs.find((p) => !p.locked) ?? packs[0];
    onSelectPack(target.pack_index);
  }, [selectedPack, packs, onSelectPack]);

  const active = packs.find((p) => p.pack_index === selectedPack) ?? null;
  // The UNION of this run's scored pages and every page that has a fix — see the block
  // comment on groupFixesByPage for the four ways a plain filter loses a customer's work.
  const { groups, sitewide } = groupFixesByPage(pages, tickets);
  const group = groups.find((g) => g.url === selectedUrl) ?? null;
  const page = group?.detail ?? null;

  return (
    <div className="flex flex-col gap-4">
      {packs.length > 1 && (
        <div className="card p-4">
          <span className="label-mono">Pages from</span>
          <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Choose which pack's pages to view">
            {packs.map((p) => (
              <button
                key={p.pack_index}
                type="button"
                onClick={() => onSelectPack(p.pack_index)}
                aria-pressed={p.pack_index === selectedPack}
                className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition-colors ${
                  p.pack_index === selectedPack
                    ? "border-accent/40 bg-accent/10 font-medium text-ink"
                    : "border-ink/10 text-ink-300 hover:border-ink/20 hover:text-ink"
                }`}
              >
                {p.locked && <span aria-hidden>🔒</span>}
                {p.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Outside the `packs.length > 1` selector above on purpose — that block is hidden on a
          single-pack run, and this sentence is exactly what a single-pack user needs to see. */}
      {lockedFixCount != null && lockedFixCount > 0 && (
        <p className="text-[12.5px] text-ink-500">
          <span aria-hidden>🔒</span> {lockedFixCount} more fix
          {lockedFixCount === 1 ? "" : "es"} {lockedFixCount === 1 ? "is" : "are"} in locked packs.
        </p>
      )}

      {selectedPack == null ? (
        <p className="rounded-xl border border-dashed border-ink/15 p-5 text-sm text-ink-500">
          Pick a pack to see how each of its pages scores.
        </p>
      ) : locked ? (
        <div className="rounded-xl border border-accent/25 bg-accent/[0.05] p-5">
          <h4 className="text-base font-semibold">{active?.title ?? `Pack ${selectedPack}`} is locked</h4>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-500">
            Unlock this pack to see how each of its pages scores on the five skills, and what to
            fix first on each one.
          </p>
          <button type="button" onClick={() => onUnlock(selectedPack)} className="btn-primary mt-3 !py-2 text-[13px]">
            Unlock {active?.title ?? `Pack ${selectedPack}`}
          </button>
        </div>
      ) : error ? (
        <p className="rounded-xl border border-dashed border-ink/15 p-5 text-sm text-ink-500">
          Couldn&apos;t load these page scores. Try again in a moment.
        </p>
      ) : pages == null ? (
        <div className="flex items-center gap-3 rounded-xl border border-ink/[0.08] bg-paper-100 p-5 text-sm text-ink-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          Loading page scores…
        </div>
      ) : groups.length === 0 ? (
        <p className="rounded-xl border border-dashed border-ink/15 p-5 text-sm text-ink-500">
          No scored pages in this pack yet.
        </p>
      ) : (
        // Single column below sm; two from sm up. A squeezed sidebar beside a squeezed
        // detail pane makes both unreadable, so small screens get a picker instead.
        <div className="grid gap-4 sm:grid-cols-[minmax(200px,260px)_minmax(0,1fr)] sm:items-start">
          {/* Mobile: a native picker. Keeps the full page list reachable in one tap. */}
          <div className="sm:hidden">
            <label className="label-mono" htmlFor="pages-picker">
              Page
            </label>
            <select
              id="pages-picker"
              value={selectedUrl ?? ""}
              onChange={(e) => setSelectedUrl(e.target.value)}
              className="input mt-1 w-full"
            >
              {groups.map((g) => (
                <option key={g.url} value={g.url}>
                  {pathOf(g.url)}
                  {g.detail?.overall != null ? ` — ${g.detail.overall}` : ""}
                  {g.tickets.length > 0 ? ` (${pageFixCounts(g.tickets).open} to do)` : ""}
                </option>
              ))}
            </select>
          </div>

          {/* Desktop: the sidebar list. */}
          <nav aria-label="Pages in this pack" className="hidden sm:block">
            <ul className="flex list-none flex-col gap-1 p-0">
              {groups.map((g) => {
                const on = g.url === selectedUrl;
                const tone = scoreTone(g.detail?.overall ?? null);
                const c = pageFixCounts(g.tickets);
                return (
                  <li key={g.url}>
                    <button
                      type="button"
                      onClick={() => setSelectedUrl(g.url)}
                      aria-current={on ? "true" : undefined}
                      className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                        on
                          ? "border-accent/40 bg-accent/[0.07]"
                          : "border-transparent hover:border-ink/10 hover:bg-paper-200/40"
                      }`}
                    >
                      <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${tone.dot}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-medium text-ink" title={g.url}>
                          {pathOf(g.url)}
                        </span>
                        <span className="label-mono !text-[10px] text-ink-300">
                          {g.detail?.page_type ?? "not scored this run"}
                          {c.total > 0 && ` · ${c.open} to do`}
                        </span>
                      </span>
                      <span className={`font-display text-[14px] font-semibold leading-none ${tone.text}`}>
                        {g.detail?.overall ?? "—"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          {/* The selected page. */}
          <div className="min-w-0">
            {page == null ? (
              <p className="text-sm text-ink-500">Pick a page to see its scores.</p>
            ) : (
              <article className="card flex flex-col gap-4 p-5">
                <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <div className="min-w-0">
                    <h4 className="m-0 truncate text-[15px] font-semibold text-ink" title={page.url}>
                      {pathOf(page.url)}
                    </h4>
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

                {group && group.tickets.length > 0 && (
                  // The FIXES for this page, and they are workable here. They used to live in
                  // "Your plan" as a second, parallel to-do surface; this tab could only link
                  // across to them. Still CLOSED by default — scores first, work one click
                  // away — but the click now lands on the thing itself.
                  //
                  // Rendered OUTSIDE the `page.detail` branch below on purpose: a page can
                  // have fixes with no scored detail this run (tickets are per client, not
                  // per run) and nesting this inside `detail != null` is exactly how those
                  // fixes disappear.
                  <details className="group/fix border-t border-white/[0.09] pt-3">
                    <summary className="cursor-pointer list-none text-[13px] text-ink-300 transition-colors hover:text-accent">
                      <span className="group-open/fix:hidden">
                        {(() => {
                          const c = pageFixCounts(group.tickets);
                          return `Show ${c.total} fix${c.total === 1 ? "" : "es"} for this page${
                            c.verified > 0 ? ` · ${c.verified} verified` : ""
                          } →`;
                        })()}
                      </span>
                      <span className="hidden group-open/fix:inline">Hide fixes</span>
                    </summary>
                    <div className="mt-3">
                      {fixError && (
                        <p role="alert" className="mb-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
                          That didn&apos;t work: {fixError}
                        </p>
                      )}
                      <ul className="m-0 flex list-none flex-col gap-1 divide-y divide-white/[0.06] p-0">
                        {group.tickets.map((t) => (
                          <PageFixRow
                            key={t.task_key}
                            ticket={t}
                            shareUrl={shareUrl}
                            busy={busyKey === t.task_key}
                            onClose={() => act(t.task_key, () => api.closeTicket(runId, t.task_key))}
                            onReopen={() => act(t.task_key, () => api.reopenTicket(runId, t.task_key))}
                            onRecheck={() => act(t.task_key, () => api.recheckTicket(runId, t.task_key))}
                          />
                        ))}
                      </ul>
                    </div>
                  </details>
                )}

                {page.detail == null ? (
                  // Honest empty state, carried over from PackDetail: a page can be in a pack
                  // without a scored payload (unreachable on the crawl). Say so rather than
                  // rendering five zeros, which would read as "we scored it and it got zero".
                  <p className="m-0 text-[13px] text-ink-300">
                    No skill scores for this page yet — it may not have been readable on the last
                    crawl.
                  </p>
                ) : (
                  <>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {SKILL_META.map((meta) => {
                        const s = page.detail!.skills[meta.key as SkillKey];
                        return s ? (
                          <SkillRow key={meta.key} label={meta.label} blurb={meta.blurb} skill={s} />
                        ) : null;
                      })}
                    </div>

                    {/* Only when this page has NO tickets. The two are mutually exclusive by
                        design: a ticket is GENERATED from these very priorities, so rendering
                        both would put an actionable row directly beneath a read-only copy of
                        itself — with a dismiss that only fires captureOverride and does not
                        touch the ticket. That is the two-parallel-lists problem item 3.4 was
                        written to remove, reintroduced one card lower down. */}
                    {(group?.tickets.length ?? 0) === 0 && page.detail.priorities.length > 0 && (
                      <details className="group border-t border-white/[0.09] pt-3">
                        <summary className="cursor-pointer list-none text-[13px] text-ink-300 transition-colors hover:text-accent">
                          <span className="group-open:hidden">
                            Show {page.detail.priorities.length} suggestion
                            {page.detail.priorities.length === 1 ? "" : "s"} for this page →
                          </span>
                          <span className="hidden group-open:inline">Hide suggestions</span>
                        </summary>
                        <div className="mt-3">
                          {/* CH-05: overrides are captured against the PAGE url, not the
                              domain — a rejected fix is only useful if we know which page. */}
                          <PriorityList priorities={page.detail.priorities} context={page.url} compact />
                        </div>
                      </details>
                    )}
                  </>
                )}
              </article>
            )}

            {/* Fixes with no page of their own. They must land SOMEWHERE visible: attaching
                them to whichever page happens to be selected would be a lie, and dropping
                them loses work the customer paid for. */}
            {sitewide.length > 0 && (
              <article className="card mt-4 flex flex-col gap-3 p-5">
                <header>
                  <h4 className="m-0 text-[15px] font-semibold text-ink">Across your whole site</h4>
                  <span className="label-mono !text-[10px] text-ink-300">
                    not tied to a single page
                  </span>
                </header>
                <ul className="m-0 flex list-none flex-col gap-1 divide-y divide-white/[0.06] p-0">
                  {sitewide.map((t) => (
                    <PageFixRow
                      key={t.task_key}
                      ticket={t}
                      shareUrl={shareUrl}
                      busy={busyKey === t.task_key}
                      onClose={() => act(t.task_key, () => api.closeTicket(runId, t.task_key))}
                      onReopen={() => act(t.task_key, () => api.reopenTicket(runId, t.task_key))}
                      onRecheck={() => act(t.task_key, () => api.recheckTicket(runId, t.task_key))}
                    />
                  ))}
                </ul>
              </article>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** One workable fix, under the page it belongs to.
 *
 *  Ported from quest/PackPlanSection.tsx's PackTaskRow when the fixes moved out of "Your
 *  plan". Deliberately NOT given the plan's 3-state segmented control: a ticket has a FOURTH
 *  state, closed_pending_verify, and moves through ACTIONS rather than a free status set —
 *  closing one enqueues a forced re-crawl and only that crawl may mark it verified (CH-15). A
 *  radio button labelled "Verified" would let a user assert a verification that has not
 *  happened, which is the precise dishonesty that loop exists to prevent. */
function PageFixRow({
  ticket,
  shareUrl,
  busy,
  onClose,
  onReopen,
  onRecheck,
}: {
  ticket: Ticket;
  shareUrl: string | null;
  busy: boolean;
  onClose: () => void;
  onReopen: () => void;
  onRecheck: () => void;
}) {
  const [open, setOpen] = useState(false);
  const verifying = ticket.status === "closed_pending_verify";
  const done = ticket.status === "verified_completed";
  const lift =
    ticket.baseline_score != null && ticket.current_score != null
      ? ticket.current_score - ticket.baseline_score
      : null;

  return (
    // The anchor both surfaces have always agreed on. Kept even though nothing links here
    // today: it is one line, it is unit-tested, and it keeps a stable deep-link target.
    <li id={packFixDomId(ticket.skill, ticket.page_url)} className="px-1 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <span className={`text-[13.5px] font-medium ${done ? "text-ink-300 line-through" : "text-ink"}`}>
            {ticket.label}
          </span>
          <p className="mt-0.5 text-[12.5px] leading-[1.5] text-ink-500">{ticket.action_required}</p>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
              aria-expanded={open}
            >
              {open ? "Hide how-to" : "Show me how →"}
            </button>
            {verifying && (
              <span className="text-ink-300">
                We&apos;re re-checking this page now — it flips to Verified once we can see the
                change live.
              </span>
            )}
            {done && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-medium text-emerald-300">
                <Check width={10} height={10} /> auto-detected live
              </span>
            )}
            {done && lift != null && lift > 0 && (
              <span className="font-mono text-emerald-300">
                {ticket.baseline_score} → {ticket.current_score} (+{lift})
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {busy ? (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink/20 border-t-accent" />
          ) : done || verifying ? (
            <>
              {verifying && (
                <button type="button" onClick={onRecheck} className="btn-ghost !py-1 text-[11px]">
                  Check again
                </button>
              )}
              <button type="button" onClick={onReopen} className="btn-ghost !py-1 text-[11px]">
                Reopen
              </button>
            </>
          ) : (
            <button type="button" onClick={onClose} className="btn-primary !py-1 text-[11px]">
              Mark as done
            </button>
          )}
        </div>
      </div>
      {open && (
        <TaskHowTo
          taskKey={ticket.task_key}
          label={ticket.label}
          actionRequired={ticket.action_required ?? ""}
          howTo={ticket.how_to ?? undefined}
          shareUrl={shareUrl}
        />
      )}
    </li>
  );
}
