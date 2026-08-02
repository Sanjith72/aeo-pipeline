"use client";

// v5 CH-02b — the unlock dialog. Two ways in, in order of prominence:
//   1. BUY this pack — flat price per pack (§9.2 resolved), via Stripe Checkout. Redirects
//      to Stripe; the entitlement is granted by the webhook, never by the browser coming
//      back, so a user who closes the tab mid-redirect still gets what they paid for.
//   2. A promo code — grants all_packs for the domain (the pre-payments path, still live).
// Shown to a LOGGED-IN user who clicks "Unlock" on a locked pack; a signed-out user is sent
// to sign in first (handled by the caller). On success the caller re-fetches packs.

import { useState } from "react";
import { api } from "@/lib/api";

export function UnlockModal({
  domain,
  packIndex,
  onUnlocked,
  onClose,
}: {
  domain: string;
  /** The pack being unlocked. Omitted → promo-only (no single pack to buy). */
  packIndex?: number;
  onUnlocked: () => void;
  onClose: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [buying, setBuying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Pack 1 is free by the unlock rule, so it is never for sale.
  const canBuy = typeof packIndex === "number" && packIndex > 1;

  async function buy() {
    if (!canBuy) return;
    setBuying(true);
    setError(null);
    try {
      const { checkout_url } = await api.checkoutPack(domain, packIndex!);
      window.location.href = checkout_url; // hand off to Stripe
    } catch (err) {
      const status = (err as { status?: number })?.status;
      setError(
        status === 503
          ? "Card payment isn't set up on this instance yet — a promo code still works."
          : status === 409
            ? "You already have this pack. Refresh to see it."
            : "Couldn't start checkout — please try again.",
      );
      setBuying(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.redeemPromo(domain, code.trim());
      onUnlocked();
    } catch (err) {
      const status = (err as { status?: number })?.status;
      setError(status === 422 ? "That code isn't valid or has expired." : "Couldn't redeem that code — please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Unlock packs"
      onClick={onClose}
    >
      <div className="card w-full max-w-[400px] p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="text-[19px] font-semibold text-ink">Unlock your packs</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-ink-300 hover:text-ink">
            ✕
          </button>
        </div>
        <p className="mb-4 text-[13.5px] leading-[1.5] text-ink-500">
          Unlock Pack {packIndex ?? ""} for <span className="text-ink">{domain}</span>
          {canBuy ? " — a one-off payment, no subscription." : "."}
        </p>
        {canBuy && (
          <>
            <button
              type="button"
              onClick={buy}
              disabled={buying || busy}
              className="btn-primary mb-4 w-full justify-center disabled:opacity-60"
            >
              {buying ? "Opening checkout…" : `Buy Pack ${packIndex}`}
            </button>
            <div className="mb-4 flex items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-white/[0.13]" />
              <span className="label-mono !text-[10px] text-ink-300">or</span>
              <span className="h-px flex-1 bg-white/[0.13]" />
            </div>
          </>
        )}
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="label-mono !text-[10px] text-ink-300" htmlFor="promo-code">
            Promo code
          </label>
          <input
            id="promo-code"
            type="text"
            autoComplete="off"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="input"
            placeholder="e.g. LAUNCH"
          />
          {error && <p className="text-[13px] text-red-400">{error}</p>}
          <button type="submit" disabled={busy} className="btn-primary mt-1 justify-center disabled:opacity-60">
            {busy ? "…" : "Unlock"}
          </button>
        </form>
      </div>
    </div>
  );
}
