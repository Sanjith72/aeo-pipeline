"use client";

// v5 CH-07 — the login / signup modal. Google is the primary path (one click, no password
// to invent — the whole point of gating is that people actually get through it); email +
// password stays as the fallback for anyone without a Google account. A modal, not a route,
// so it never unmounts live audit/plan React state — except for the Google leg, which is a
// full-page redirect and lands on /auth/callback. Uses existing design tokens (.card,
// .input, .btn-primary, label-mono). Rendered once at AuthProvider level; only mounted when
// Supabase is configured.

import { useState } from "react";
import { api } from "@/lib/api";
import { currentAccessToken, signInWithGoogle, supabase } from "@/lib/supabase";
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
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (!authOpen) return null;

  async function google() {
    setGoogleBusy(true);
    setError(null);
    setNotice(null);
    // Resolves only if the redirect never happened — on success the tab is already gone.
    const { error: err } = await signInWithGoogle();
    if (err) {
      setError(err);
      setGoogleBusy(false);
    }
  }

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
        <button
          type="button"
          onClick={google}
          disabled={googleBusy || busy}
          className="btn-ghost mb-4 w-full gap-2.5 py-3 text-[15px] text-ink"
        >
          <GoogleMark />
          {googleBusy ? "Redirecting…" : "Continue with Google"}
        </button>
        <div className="mb-4 flex items-center gap-3" aria-hidden="true">
          <span className="h-px flex-1 bg-white/[0.13]" />
          <span className="label-mono !text-[10px] text-ink-300">or</span>
          <span className="h-px flex-1 bg-white/[0.13]" />
        </div>
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

/** Google's four-colour "G". Inline SVG (no network fetch, no external asset) and marked
 *  aria-hidden — the button's own text is the accessible name. */
function GoogleMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path
        fill="#4285F4"
        d="M45.12 24.5c0-1.56-.14-3.06-.4-4.5H24v8.51h11.84c-.51 2.75-2.06 5.08-4.39 6.64v5.52h7.11c4.16-3.83 6.56-9.47 6.56-16.17z"
      />
      <path
        fill="#34A853"
        d="M24 46c5.94 0 10.92-1.97 14.56-5.33l-7.11-5.52c-1.97 1.32-4.49 2.1-7.45 2.1-5.73 0-10.58-3.87-12.31-9.07H4.34v5.7C7.96 41.07 15.4 46 24 46z"
      />
      <path
        fill="#FBBC05"
        d="M11.69 28.18c-.44-1.32-.69-2.73-.69-4.18s.25-2.86.69-4.18v-5.7H4.34A21.99 21.99 0 0 0 2 24c0 3.55.85 6.91 2.34 9.88l7.35-5.7z"
      />
      <path
        fill="#EA4335"
        d="M24 10.75c3.23 0 6.13 1.11 8.41 3.29l6.31-6.31C34.91 4.18 29.93 2 24 2 15.4 2 7.96 6.93 4.34 14.12l7.35 5.7c1.73-5.2 6.58-9.07 12.31-9.07z"
      />
    </svg>
  );
}
