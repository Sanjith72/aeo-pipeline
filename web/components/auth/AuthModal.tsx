"use client";

// v5 CH-07 — the login / signup modal. Google is the primary path (one click, no password
// to invent — the whole point of gating is that people actually get through it); email +
// password stays as the fallback for anyone without a Google account. A modal, not a route,
// so it never unmounts live audit/plan React state — except for the Google leg, which is a
// full-page redirect and lands on /auth/callback. Uses existing design tokens (.card,
// .input, .btn-primary, label-mono). Rendered once at AuthProvider level; only mounted when
// Supabase is configured.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { classifySignUpFailure, resendCooldownRemaining } from "@/lib/authCallback";
import { currentAccessToken, signInWithGoogle, supabase } from "@/lib/supabase";
import { readPendingUnlock, unlockReturnPath } from "@/lib/pendingUnlock";
import { useAuth } from "./AuthProvider";

/** Where this sign-in should land when it involves a full page load. Read at CLICK time,
 *  not render time — the pending-unlock record is written by the Unlock button moments
 *  before this modal opens, and it names the one page (/plan/<id>) that can rebuild the
 *  pack context after the round-trip. No pending unlock → back to the current page. */
function returnNext(): string {
  return unlockReturnPath(window.location.pathname + window.location.search, readPendingUnlock());
}

type Mode = "signin" | "signup";

const REASON_COPY: Record<string, string> = {
  "unlock-pack": "Sign in to unlock this pack and see its page-by-page fixes.",
  "go-deeper": "Sign in to save your plan and unlock the full page-by-page report.",
};

export function AuthModal() {
  const { authOpen, authReason, closeAuth, user } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [googleBusy, setGoogleBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // "we sent you a confirmation mail" is its own state, not a notice string: it is the only
  // state that can offer a resend, and it must read differently from a failure. Previously
  // both were the same grey line, so "check your email" and "that didn't work" were
  // indistinguishable at a glance.
  const [awaitingConfirm, setAwaitingConfirm] = useState<string | null>(null);
  const [resending, setResending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const lastSentAt = useRef<number | null>(null);

  // Tick the resend cooldown down. Supabase rate-limits confirmation mail to roughly one
  // per minute, so a button that can be hammered only produces errors the user cannot act on.
  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setInterval(
      () => setCooldown(resendCooldownRemaining(lastSentAt.current, Date.now())),
      1000,
    );
    return () => clearInterval(id);
  }, [cooldown]);

  // Sign-in can complete OUTSIDE this modal: the email-confirmation link opens in a new
  // tab, that tab's session syncs across, and `user` appears here while the modal still
  // says "Confirm your email" — painting over the unlock dialog the redeem effect just
  // opened underneath it (same z-index, mounted later). Signed in means this modal's job
  // is done, whoever finished it.
  useEffect(() => {
    if (user && authOpen) {
      setAwaitingConfirm(null);
      closeAuth();
    }
  }, [user, authOpen, closeAuth]);

  const resendConfirmation = useCallback(async () => {
    if (!supabase || !awaitingConfirm || cooldown > 0) return;
    setResending(true);
    setError(null);
    const { error: err } = await supabase.auth.resend({
      type: "signup",
      email: awaitingConfirm,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(returnNext())}` },
    });
    setResending(false);
    if (err) {
      setError(classifySignUpFailure(err.message).message);
      return;
    }
    lastSentAt.current = Date.now();
    setCooldown(resendCooldownRemaining(lastSentAt.current, Date.now()));
    setNotice("Sent — check your inbox (and your spam folder).");
  }, [awaitingConfirm, cooldown]);

  if (!authOpen) return null;

  async function google() {
    setGoogleBusy(true);
    setError(null);
    setNotice(null);
    // Resolves only if the redirect never happened — on success the tab is already gone.
    const { error: err } = await signInWithGoogle(returnNext());
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
        const { data, error: err } = await supabase.auth.signUp({
          email,
          password,
          // Without this, `{{ .RedirectTo }}` is empty in the confirmation template and
          // GoTrue falls back to the project's bare Site URL — so the link lands on the
          // homepage instead of the callback that can actually complete the sign-in.
          options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(returnNext())}` },
        });
        if (err) throw err;
        if (!data.session) {
          // Confirmation required. Stay on this screen with a resend affordance rather than
          // flipping to sign-in — the user cannot sign in yet, and being dropped on a form
          // that will reject them is what made this read as a failure.
          setAwaitingConfirm(email);
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
      // Was the raw exception string, so the most common and most fixable case —
      // "User already registered" — read like an internal error instead of "you already
      // have an account, sign in".
      const raw = err instanceof Error ? err.message : String(err);
      setError(classifySignUpFailure(raw).message);
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
            {awaitingConfirm ? "Confirm your email" : mode === "signin" ? "Sign in" : "Create your account"}
          </h2>
          <button type="button" onClick={closeAuth} aria-label="Close" className="text-ink-300 hover:text-ink">
            ✕
          </button>
        </div>
        {awaitingConfirm ? (
          <>
            <p className="mb-4 text-[13.5px] leading-[1.5] text-ink-500">
              We sent a confirmation link to <strong className="text-ink">{awaitingConfirm}</strong>.
              Open it and you&apos;ll be signed in automatically.
            </p>
            <p className="mb-4 text-[13px] leading-[1.5] text-ink-300">
              Links are single-use, and email security scanners sometimes open them before you
              do. If yours doesn&apos;t work, send a fresh one.
            </p>
            {error && <p className="mb-3 text-[13px] text-red-400">{error}</p>}
            {notice && (
              <p className="mb-3 text-[13px] text-accent" role="status">
                {notice}
              </p>
            )}
            <button
              type="button"
              onClick={() => void resendConfirmation()}
              disabled={resending || cooldown > 0}
              className="btn-primary w-full justify-center disabled:opacity-60"
            >
              {resending ? "Sending…" : cooldown > 0 ? `Resend (${cooldown}s)` : "Resend the link"}
            </button>
            <button
              type="button"
              onClick={() => {
                setAwaitingConfirm(null);
                setMode("signin");
                setError(null);
                setNotice(null);
              }}
              className="mt-4 text-[13px] text-ink-300 underline-offset-2 hover:text-ink hover:underline"
            >
              Back to sign in
            </button>
          </>
        ) : (
          <>
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
          </>
        )}
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
