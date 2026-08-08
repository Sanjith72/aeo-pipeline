"use client";

// The ONE owner of a pack's workable state — pages, tickets, the verify poll, and the
// close/reopen/recheck actions — shared by the Pages tab and "Your plan".
//
// It exists because the two surfaces used to each fetch and poll /api/tickets/* on their
// own (PagesPanel and the old PackPlanSection), so marking a fix done on one left the
// other stale until its own poll happened to fire, and two 5-second pollers ran for one
// pack. Instantiated once in ResultsView — the same reason ResultsView lifts shareUrl:
// state that two tabs render must have one owner, or the tabs disagree.
//
// A locked pack is a 403 by DESIGN (CH-02a): the server refuses to ship a locked pack's
// data, and this hook reports it as `locked`, never as an error.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { PackPageDetail, Ticket } from "@/lib/types";

/** How often to re-read tickets while any of them is awaiting its verification crawl. */
const VERIFY_POLL_MS = 5000;

export interface PackTicketsState {
  /** The selected pack's scored pages; null while loading. */
  pages: PackPageDetail[] | null;
  /** The selected pack's tickets; null while loading. */
  tickets: Ticket[] | null;
  /** The selected pack is locked (server 403) — a product state, not a failure. */
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
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [lockedFixCount, setLockedFixCount] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPages = useCallback(async () => {
    if (runId == null || selectedPack == null) return;
    setLocked(false);
    setError(false);
    setPages(null);
    try {
      const res = await api.getPackDetail(runId, selectedPack);
      setPages(res.pages);
    } catch (e) {
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

  const loadTickets = useCallback(async () => {
    if (runId == null || selectedPack == null) return;
    try {
      const res = await api.getPackTickets(runId, selectedPack);
      setTickets(res.tickets);
      setLocked(false); // a successful read IS the proof the pack is not locked
      setFixError(null);
    } catch (e) {
      if ((e as { status?: number })?.status === 403) {
        setLocked(true);
        setTickets([]);
        return;
      }
      setFixError(e instanceof Error ? e.message : String(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, selectedPack, lockedKey]);

  useEffect(() => {
    void loadPages();
  }, [loadPages]);

  useEffect(() => {
    setTickets(null);
    void loadTickets();
  }, [loadTickets]);

  // "N more fixes in locked packs" — the count the server computes for exactly that
  // sentence. Keyed on the LOCKED SET, exactly as the pre-refactor code was: unlocking a
  // pack must drop the count, and nothing else the hook sees changes on an unlock.
  useEffect(() => {
    if (runId == null) return;
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

  // Poll only while something is actually awaiting its crawl, and PACK-WIDE — filtering
  // this to one page would stop polling a fix the moment the user looked at another page.
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

  return { pages, tickets, locked, error, fixError, busyKey, lockedFixCount, close, reopen, recheck, reloadTickets };
}
