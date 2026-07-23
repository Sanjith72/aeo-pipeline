"use client";

// v5 CH-07 — the login / signup modal (email + password via Supabase). A modal, not a
// route, so it never unmounts live audit/plan React state. Uses existing design tokens
// (.card, .input, .btn-primary, label-mono). Rendered once at AuthProvider level; only
// mounted when Supabase is configured.

import { useState } from "react";
import { api } from "@/lib/api";
import { currentAccessToken, supabase } from "@/lib/supabase";
import { useAuth } from "./AuthProvider";

type Mode = "signin" | "signup";

const REASON_COPY: Record<string, string> = {
  "unlock-pack": "Sign in to unlock this pack and see its page-by-page fixes.",
  "go-deeper": "Sign in to save your plan and unlock the full page-by-page report.",
};

export function AuthModal() {
  const { authOpen, authReason, closeAuth } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!authOpen) return null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "signup") {
        const { data, error: err } = await supabase.auth.signUp({ email, password });
        if (err) throw err;
        if (!data.session) {
          setNotice("Check your email to confirm your account, then sign in.");
          setMode("signin");
          return;
        }
      } else {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) throw err;
      }
      // Ensure the token is live, then provision + claim the anonymous session server-side.
      await currentAccessToken();
      try {
        // Provisions the user + claims the pre-auth aeo_sid session (cookie-sourced server-side).
        await api.me();
      } catch {
        /* provisioning is best-effort; the session still works */
      }
      closeAuth();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Sign in"
      onClick={closeAuth}
    >
      <div className="card w-full max-w-[400px] p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="text-[19px] font-semibold text-ink">
            {mode === "signin" ? "Sign in" : "Create your account"}
          </h2>
          <button type="button" onClick={closeAuth} aria-label="Close" className="text-ink-300 hover:text-ink">
            ✕
          </button>
        </div>
        {authReason && REASON_COPY[authReason] && (
          <p className="mb-4 text-[13.5px] leading-[1.5] text-ink-500">{REASON_COPY[authReason]}</p>
        )}
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="label-mono !text-[10px] text-ink-300" htmlFor="auth-email">
            Email
          </label>
          <input
            id="auth-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input"
          />
          <label className="label-mono !text-[10px] text-ink-300" htmlFor="auth-password">
            Password
          </label>
          <input
            id="auth-password"
            type="password"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input"
          />
          {error && <p className="text-[13px] text-red-400">{error}</p>}
          {notice && <p className="text-[13px] text-accent">{notice}</p>}
          <button type="submit" disabled={busy} className="btn-primary mt-1 justify-center disabled:opacity-60">
            {busy ? "…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>
        <button
          type="button"
          onClick={() => {
            setMode((m) => (m === "signin" ? "signup" : "signin"));
            setError(null);
            setNotice(null);
          }}
          className="mt-4 text-[13px] text-ink-300 underline-offset-2 hover:text-ink hover:underline"
        >
          {mode === "signin" ? "New here? Create an account" : "Already have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}
