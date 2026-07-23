"use client";

// v5 CH-02b — the promo-code unlock dialog (payments stubbed; a valid code grants an
// all_packs entitlement for the domain server-side). Shown to a LOGGED-IN user who clicks
// "Unlock" on a locked pack; a signed-out user is sent to sign in first (handled by the
// caller). On success the caller re-fetches packs so the locks recompute.

import { useState } from "react";
import { api } from "@/lib/api";

export function UnlockModal({
  domain,
  onUnlocked,
  onClose,
}: {
  domain: string;
  onUnlocked: () => void;
  onClose: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
          Have a promo code? Enter it to unlock every pack for <span className="text-ink">{domain}</span>.
        </p>
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
