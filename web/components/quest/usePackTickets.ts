"use client";

// The ONE owner of a run's workable state — the selected pack's pages, EVERY unlocked
// pack's tickets, the verify poll, and the close/reopen/recheck actions — shared by the
// Pages tab and "Your plan".
//
// It exists because the two surfaces used to each fetch and poll /api/tickets/* on their
// own (PagesPanel and the old PackPlanSection), so marking a fix done on one left the
// other stale until its own poll happened to fire, and two 5-second pollers ran for one
// pack. Instantiated once in ResultsView — the same reason ResultsView lifts shareUrl:
// state that two tabs render must have one owner, or the tabs disagree.
//
// Tickets are read RUN-WIDE (GET /api/tickets/{run}), not per pack: "Your plan" renders
// every unlocked pack's fixes under its own pack card, and N per-pack fetches describing
// one list is how surfaces drift. The server filters that route to the viewer's unlocked
// packs and reports `locked_ticket_count` for what it withheld — so a locked pack's
// tickets are simply absent here, never an error.
//
// The page DETAIL stays per-pack (GET /api/packs/{run}/{pack}) because only the selected
// pack's scores are on screen at once — and a locked pack answers 403 by DESIGN (CH-02a),
// which this hook reports as `locked`, never as an error.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { PackPageDetail, Ticket } from "@/lib/types";

/** How often to re-read tickets while any of them is awaiting its verification crawl. */
const VERIFY_POLL_MS = 5000;

export interface PackTicketsState {
  /** The selected pack's scored pages; null while loading. */
  pages: PackPageDetail[] | null;
  /** The selected pack's tickets — a view over `allTickets`; null while loading. */
  tickets: Ticket[] | null;
  /** Every unlocked pack's tickets for the run (each knows its pack_index); null while
   *  loading. What "Your plan" groups under each pack. */
  allTickets: Ticket[] | null;
  /** True when `allTickets` reflects the CURRENT locked set. False while the refetch a
   *  just-changed entitlement triggers is still in flight (or failed) — the window where a
   *  freshly unlocked pack has no rows in the list simply because the list predates the
   *  purchase. Callers must not read "no rows" as "nothing to do" unless this is true. */
  ticketsFresh: boolean;
  /** The selected pack is locked (server 403 on its page detail) — a product state, not a
   *  failure. */
  locked: boolean;
  /** The page-detail fetch genuinely failed (network/5xx — not a lock). */
  error: boolean;
  /** A ticket action or the ticket fetch failed; human-readable. */
  fixError: string | null;
  /** task_key of the action in flight, for per-row spinners. */
  busyKey: string | null;
  /** Server-computed "N more fixes in locked packs"; null = unknown, never "0 more". */
  lockedFixCount: number | null;
  close: (taskKey: string) => void;
  reopen: (taskKey: string) => void;
  recheck: (taskKey: string) => void;
  /** Re-read tickets without resetting page selection (e.g. after an unlock). */
  reloadTickets: () => void;
}

export function usePackTickets(
  runId: number | null,
  selectedPack: number | null,
  /** The locked-pack set as a string (e.g. "2,3"), derived from the packs list the caller
   *  refreshes after an unlock. It is a dependency of every fetch here for one reason: an
   *  in-page promo redemption changes NOTHING else this hook can see — without it, the
   *  403-latched `locked` flag and the pages/tickets it suppressed stay stale forever, and
   *  the just-paid pack keeps rendering "locked" on both tabs until a full reload. */
  lockedKey = "",
): PackTicketsState {
  const [pages, setPages] = useState<PackPageDetail[] | null>(null);
  const [allTickets, setAllTickets] = useState<Ticket[] | null>(null);
  // The lockedKey the current allTickets was fetched under — the freshness marker. A list
  // read before an unlock must never be mistaken for a statement about the post-unlock
  // world (it is missing the just-paid pack's rows).
  const [ticketsKey, setTicketsKey] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [lockedFixCount, setLockedFixCount] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Monotonic fetch sequence — LATEST WINS. Responses are applied only if no newer fetch
  // has started since; without this, a slow pre-unlock read resolving after the fast
  // post-unlock one would overwrite the fresh list with the stale one, and a just-paid
  // pack would render "nothing to fix" until a reload (the exact class of stale-latch bug
  // the lockedKey dependency exists to prevent).
  const ticketsSeq = useRef(0);
  const pagesSeq = useRef(0);

  const loadPages = useCallback(async () => {
    if (runId == null || selectedPack == null) return;
    const seq = ++pagesSeq.current;
    setLocked(false);
    setError(false);
    setPages(null);
    try {
      const res = await api.getPackDetail(runId, selectedPack);
      if (seq !== pagesSeq.current) return; // a newer fetch owns the state now
      setPages(res.pages);
    } catch (e) {
      if (seq !== pagesSeq.current) return;
      if ((e as { status?: number })?.status === 403) {
        setLocked(true);
        setPages([]);
        return;
      }
      setError(true);
    }
    // lockedKey: an unlock flips entitlements without touching runId/selectedPack — the
    // refetch it triggers here is what clears a stale `locked` and loads the paid pages.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, selectedPack, lockedKey]);

  // The run-wide read: every unlocked pack's tickets plus the withheld count, in one
  // response. Deliberately NOT keyed on selectedPack — switching packs re-renders a
  // different slice of the same list rather than refetching it.
  const loadTickets = useCallback(async () => {
    if (runId == null) return;
    const seq = ++ticketsSeq.current;
    try {
      const res = await api.getTickets(runId);
      if (seq !== ticketsSeq.current) return; // a newer fetch owns the state now
      setAllTickets(res.tickets);
      setTicketsKey(lockedKey);
      setLockedFixCount(res.locked_ticket_count ?? 0);
      setFixError(null);
    } catch (e) {
      if (seq !== ticketsSeq.current) return;
      // The last successful list AND count stay up — both are still true statements about
      // the last read, unlike a blank. Only the error message changes.
      setFixError(e instanceof Error ? e.message : String(e));
    }
    // lockedKey: a just-unlocked pack's tickets appear in this route's response only after
    // the entitlement flip — this is what fetches them without a reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, lockedKey]);

  useEffect(() => {
    void loadPages();
  }, [loadPages]);

  useEffect(() => {
    void loadTickets();
  }, [loadTickets]);

  /** The selected pack's slice, in the shape PagesPanel has always consumed. A locked
   *  selected pack derives to [] because the server withheld its rows — same outcome the
   *  old per-pack 403 produced. */
  const tickets = useMemo(() => {
    if (allTickets == null) return null;
    if (selectedPack == null) return [];
    return allTickets.filter((t) => t.pack_index === selectedPack);
  }, [allTickets, selectedPack]);

  // Poll only while something is actually awaiting its crawl, and RUN-WIDE — filtering
  // this to one pack would stop polling a fix the moment the user switched packs (the
  // per-page version of the same bug is why the old poll was already pack-wide).
  const anyVerifying = allTickets?.some((t) => t.status === "closed_pending_verify") ?? false;
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

  const act = useCallback(
    async (taskKey: string, fn: () => Promise<unknown>) => {
      setBusyKey(taskKey);
      setFixError(null);
      try {
        await fn();
        await loadTickets(); // NOT loadPages() — that would reset callers' page selection
      } catch (e) {
        setFixError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyKey(null);
      }
    },
    [loadTickets],
  );

  const close = useCallback(
    (taskKey: string) => {
      if (runId == null) return;
      void act(taskKey, () => api.closeTicket(runId, taskKey));
    },
    [act, runId],
  );
  const reopen = useCallback(
    (taskKey: string) => {
      if (runId == null) return;
      void act(taskKey, () => api.reopenTicket(runId, taskKey));
    },
    [act, runId],
  );
  const recheck = useCallback(
    (taskKey: string) => {
      if (runId == null) return;
      void act(taskKey, () => api.recheckTicket(runId, taskKey));
    },
    [act, runId],
  );
  const reloadTickets = useCallback(() => void loadTickets(), [loadTickets]);

  return {
    pages,
    tickets,
    allTickets,
    ticketsFresh: allTickets != null && ticketsKey === lockedKey,
    locked,
    error,
    fixError,
    busyKey,
    lockedFixCount,
    close,
    reopen,
    recheck,
    reloadTickets,
  };
}
