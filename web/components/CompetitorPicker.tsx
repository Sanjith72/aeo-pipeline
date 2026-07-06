"use client";

// The competitor step: we recommend likely competitors from the business name,
// industry, and location — the user just ticks the ones that fit. Manual entry needs
// a NAME only; a website is optional. No URLs are ever required.

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { classifySuggestResponse, unavailableCopy } from "@/lib/suggest";
import type { CompetitorPick, CompetitorSuggestion } from "@/lib/types";
import { Check, Plus, Refresh, Search, Sparkle, X } from "./ui/icons";
import { LiquidButton } from "./ui/liquid-glass";

type SuggestState =
  | { status: "idle" | "loading" }
  | { status: "ready"; items: CompetitorSuggestion[] }
  | { status: "unavailable"; reason?: string }
  | { status: "error" };

// Session-level cache so revisiting the step doesn't refetch the same brief. Only
// non-empty results are cached: with the backend's relaxation ladder a genuine empty
// list is rare and usually means a transient timeout / data gap, so we let the UI
// self-heal by refetching on the next visit instead of pinning a blank state.
const suggestionCache = new Map<string, CompetitorSuggestion[]>();

// How many of the top suggestions get pre-ticked when they arrive. The API returns
// most-relevant-first, so ticking the head of the list starts the user from a sensible
// default they prune, instead of an all-unticked wall they must build up from zero.
const AUTO_SELECT_COUNT = 3;
// Briefs (cacheKeys) that already had their one-time auto-select — module-level so a
// step-remount doesn't re-tick suggestions the user deliberately cleared.
const autoSelected = new Set<string>();

export function CompetitorPicker({
  businessName,
  category,
  location,
  domain,
  services,
  selected,
  onChange,
}: {
  businessName: string;
  category: string;
  location: string;
  domain: string;
  services: string[];
  selected: CompetitorPick[];
  onChange: (next: CompetitorPick[]) => void;
}) {
  const [suggest, setSuggest] = useState<SuggestState>({ status: "idle" });
  const [query, setQuery] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualSite, setManualSite] = useState("");
  const [showSite, setShowSite] = useState(false);
  const requestSeq = useRef(0);
  // The latest selection, readable from the async fetch closure without going stale.
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  // Auto-select must never write into a brief the user can't see: a slow suggest fetch
  // can resolve after the user skipped this step (picker unmounted), or a stale
  // instance's fetch can land after the brief changed — both would silently inject
  // competitors into the plan.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const cacheKey = [businessName, category, location, domain, services.join(",")]
    .map((s) => s.trim().toLowerCase())
    .join("|");
  const cacheKeyRef = useRef(cacheKey);
  cacheKeyRef.current = cacheKey;

  // Pre-tick the top suggestions ONCE per brief, and only when the user has picked
  // nothing yet — never fight a selection they've already made or deliberately cleared.
  const [autoPicked, setAutoPicked] = useState(false);
  function autoSelectTop(items: CompetitorSuggestion[], forKey: string) {
    if (autoSelected.has(forKey)) return;
    // Consume the brief's one-time auto-select even when suppressed below: suggestions
    // that arrived while the user already had picks must not tick on a later remount
    // after they deliberately cleared everything.
    autoSelected.add(forKey);
    if (!mountedRef.current || forKey !== cacheKeyRef.current) return;
    if (selectedRef.current.length > 0) return;
    setAutoPicked(true);
    onChange(
      items.slice(0, AUTO_SELECT_COUNT).map((s) => ({
        name: s.name,
        domain: s.domain || undefined,
        source: "suggested" as const,
      })),
    );
  }

  async function fetchSuggestions(force = false) {
    if (!businessName.trim()) return;
    if (!force) {
      const cached = suggestionCache.get(cacheKey);
      if (cached && cached.length > 0) {
        setSuggest({ status: "ready", items: cached });
        autoSelectTop(cached, cacheKey);
        return;
      }
    }
    const seq = ++requestSeq.current;
    const keyAtFetch = cacheKey; // the brief this request was issued for
    setSuggest({ status: "loading" });
    try {
      const res = await api.suggestCompetitors({
        name: businessName.trim(),
        category: category.trim() || undefined,
        location: location.trim() || undefined,
        domain: domain.trim() || undefined,
        services: services.length > 0 ? services : undefined,
        count: 8,
      });
      if (seq !== requestSeq.current) return; // a newer request superseded this one
      // lib/suggest.ts owns the reason→state contract (unit-tested there): llm_failed
      // is the amber retry state, every other blank is neutral "unavailable".
      const outcome = classifySuggestResponse(res);
      if (outcome.kind === "ready") {
        suggestionCache.set(keyAtFetch, res.competitors);
        setSuggest({ status: "ready", items: res.competitors });
        autoSelectTop(res.competitors, keyAtFetch);
      } else if (outcome.kind === "error") {
        setSuggest({ status: "error" });
      } else {
        // Don't cache the blank — a later visit refetches and may succeed.
        setSuggest({ status: "unavailable", reason: outcome.reason });
      }
    } catch {
      if (seq === requestSeq.current) setSuggest({ status: "error" });
    }
  }

  useEffect(() => {
    fetchSuggestions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  const isPicked = (name: string) =>
    selected.some((c) => c.name.toLowerCase() === name.trim().toLowerCase());

  function toggleSuggestion(s: CompetitorSuggestion) {
    if (isPicked(s.name)) {
      onChange(selected.filter((c) => c.name.toLowerCase() !== s.name.toLowerCase()));
    } else {
      onChange([...selected, { name: s.name, domain: s.domain, source: "suggested" }]);
    }
  }

  function addManual() {
    const name = manualName.trim();
    if (!name || isPicked(name)) {
      setManualName("");
      return;
    }
    const site = manualSite.trim();
    onChange([...selected, { name, domain: site || undefined, source: "manual" }]);
    setManualName("");
    setManualSite("");
  }

  const items = suggest.status === "ready" ? suggest.items : [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? items.filter((s) => s.name.toLowerCase().includes(q) || s.domain.includes(q)) : items;
  }, [items, query]);

  return (
    <div className="space-y-7">
      {/* ── recommended ─────────────────────────────────────────────────── */}
      <div>
        <div className="mb-3 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-sm font-medium text-ink">
            <Sparkle className="text-accent" />
            Recommended for you
          </span>
          {(suggest.status === "ready" || suggest.status === "error" || suggest.status === "unavailable") && (
            <button
              type="button"
              onClick={() => fetchSuggestions(true)}
              className="btn-ghost !rounded-lg !px-2.5 !py-1.5 text-xs"
            >
              <Refresh /> Refresh
            </button>
          )}
        </div>

        {suggest.status === "loading" && (
          <div>
            <p className="mb-3 flex items-center gap-2 text-sm text-ink-500">
              <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              Finding businesses like yours…
            </p>
            <div className="grid gap-2.5 sm:grid-cols-2">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="skeleton h-[58px]" style={{ animationDelay: `${i * 120}ms` }} />
              ))}
            </div>
          </div>
        )}

        {suggest.status === "ready" && (
          <>
            {autoPicked && (
              <p className="mb-3 text-sm text-ink-500">
                We pre-selected the closest matches — untick any that don&apos;t fit.
              </p>
            )}
            {items.length > 5 && (
              <div className="relative mb-3">
                <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" />
                <input
                  className="input !pl-9"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search recommendations…"
                  aria-label="Search recommended competitors"
                />
              </div>
            )}
            <div className="grid gap-2.5 sm:grid-cols-2">
              {visible.map((s, i) => {
                const on = isPicked(s.name);
                return (
                  <button
                    key={s.domain || s.name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleSuggestion(s)}
                    className={`option-card ${on ? "option-card-on" : "option-card-off"} step-in`}
                    style={{ animationDelay: `${Math.min(i, 7) * 50}ms` }}
                  >
                    <span
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
                        on ? "border-accent bg-accent text-paper" : "border-ink/25 bg-paper-100"
                      }`}
                    >
                      {on && <Check className="animate-pop" width={12} height={12} />}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{s.name}</span>
                      {s.domain && <span className="block truncate font-mono text-xs text-ink-300">{s.domain}</span>}
                    </span>
                  </button>
                );
              })}
              {visible.length === 0 && (
                <p className="text-sm text-ink-300 sm:col-span-2">No matches — try a different search or add one below.</p>
              )}
            </div>
          </>
        )}

        {suggest.status === "unavailable" && (
          <p className="rounded-xl border border-ink/10 bg-paper-200/60 px-4 py-3 text-sm text-ink-500">
            {unavailableCopy(suggest.reason)}
          </p>
        )}

        {/* Reachable via a stepper jump before the name is typed — without this branch
            the section renders a bare heading with no explanation and no way forward.
            (With a name present, idle lasts one pre-effect frame — render nothing.) */}
        {suggest.status === "idle" && !businessName.trim() && (
          <p className="rounded-xl border border-ink/10 bg-paper-200/60 px-4 py-3 text-sm text-ink-500">
            Add your business name first — recommendations are built from it.
          </p>
        )}

        {suggest.status === "error" && (
          <p className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
            We couldn't load recommendations right now. Try refresh, or add competitors below.
          </p>
        )}
      </div>

      {/* ── manual add (names only — websites optional) ─────────────────── */}
      <div>
        <span className="mb-3 block text-sm font-medium text-ink">Add your own</span>
        <div className="flex flex-col gap-2.5 sm:flex-row">
          <input
            className="input flex-1"
            value={manualName}
            onChange={(e) => setManualName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addManual();
              }
            }}
            placeholder="Competitor name (that's all we need)"
            aria-label="Competitor name"
          />
          {showSite && (
            <input
              className="input flex-1 step-in"
              value={manualSite}
              onChange={(e) => setManualSite(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addManual();
                }
              }}
              placeholder="theirwebsite.com (optional)"
              aria-label="Competitor website, optional"
            />
          )}
          <LiquidButton
            variant="secondary"
            className="shrink-0 self-start px-6 py-3 sm:self-auto sm:py-0"
            onClick={addManual}
            disabled={!manualName.trim()}
          >
            <Plus /> Add
          </LiquidButton>
        </div>
        {!showSite && (
          <button
            type="button"
            onClick={() => setShowSite(true)}
            className="mt-2 text-xs text-ink-300 underline-offset-2 transition-colors hover:text-accent hover:underline"
          >
            + I know their website too (optional)
          </button>
        )}
      </div>

      {/* ── current picks ───────────────────────────────────────────────── */}
      {selected.length > 0 ? (
        <div>
          <span className="mb-3 block text-sm font-medium text-ink">
            Comparing against <span className="font-mono text-accent">{selected.length}</span>{" "}
            {selected.length === 1 ? "competitor" : "competitors"}
          </span>
          <div className="flex flex-wrap gap-2">
            {selected.map((c) => (
              <span key={c.name.toLowerCase()} className="chip animate-pop">
                {c.name}
                {c.domain && <span className="font-mono text-xs text-ink-300">{c.domain}</span>}
                <button
                  type="button"
                  aria-label={`Remove ${c.name}`}
                  onClick={() => onChange(selected.filter((x) => x.name !== c.name))}
                  className="rounded-full p-1 text-ink-300 transition-colors hover:bg-ink/[0.06] hover:text-ink"
                >
                  <X width={12} height={12} />
                </button>
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-ink-300">
          None selected yet — that's okay. You can skip this step and we'll plan from your goals alone.
        </p>
      )}
    </div>
  );
}
