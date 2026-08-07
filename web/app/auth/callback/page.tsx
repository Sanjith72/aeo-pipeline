"use client";

// v5 CH-07 — the auth return leg. Four different link shapes land here:
//
//   ?token_hash=&type=   the email confirm/recovery link (Supabase's cross-device form)
//   ?code=               the PKCE OAuth return from Google
//   #access_token=       the legacy implicit form
//   ?error=/#error=      a refused consent, an expired link, a non-allowlisted redirect
//
// Only the second was ever handled. The confirm-email link fell through to the PKCE branch,
// which waits for a session that nothing is going to produce, and told the user 15 seconds
// later that their sign-in "didn't come back with a session" — indistinguishable from a
// broken app. Which shape a URL is, and what a given failure means, is decided by the pure
// module in lib/authCallback.ts (tested); this route is the wiring around it.
//
// A route, not a modal, because these are all full-page redirects — there is no live React
// state left to preserve by the time we land here.

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import {
  classifyAuthFailure,
  readCallback,
  resendCooldownRemaining,
  safeNext,
  timeoutFailure,
  verifierMissingFailure,
  type Failure,
} from "@/lib/authCallback";
import { authEnabled, supabase } from "@/lib/supabase";

/** How long to wait for an exchange we cannot observe before calling it failed. Only ever
 *  reached by the `?code=` path — every other shape resolves definitively. */
const PKCE_TIMEOUT_MS = 15000;

function CallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [cooldown, setCooldown] = useState(0);
  const lastSentAt = useRef<number | null>(null);
  // Whether the sign-in question has been ANSWERED, across effect RE-RUNS. supabase-js strips
  // the `?code=` with history.replaceState once it processes it, Next resyncs
  // useSearchParams, and this effect runs again with a fresh closure — one that sees no code,
  // so it would arm the timeout and, 15 seconds later, overwrite a precise error message with
  // the generic "didn't come back with a session". A ref is the only guard that survives that.
  const answered = useRef(false);
  const next = safeNext(
    params.get("next"),
    typeof window === "undefined" ? "http://localhost" : window.location.origin,
  );

  useEffect(() => {
    let cancelled = false;
    // Within ONE run: getSession() and onAuthStateChange can both report the same session,
    // so terminal paths guard on this rather than on `cancelled`. Across runs, `answered`
    // (a ref) is what holds.
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const fail = (f: Failure) => {
      if (cancelled || settled || answered.current) return;
      settled = true;
      answered.current = true;
      if (timer) clearTimeout(timer);
      setFailure(f);
    };

    if (answered.current) return;  // a previous run already resolved this sign-in
    if (!authEnabled || !supabase) {
      router.replace(next);
      return;
    }
    const client = supabase;

    // The URL is read ONCE, here. supabase-js strips both the `?code=` and the `#fragment`
    // as soon as it processes them, so anything read later is already gone — which is why
    // the fragment error shape had to be captured before any await.
    const shape = readCallback(window.location.search, window.location.hash);

    if (shape.kind === "provider_error") {
      // eslint-disable-next-line no-console -- the only breadcrumb a user can send back
      console.error("[auth/callback] provider returned an error", shape.code, shape.description);
      setFailure(classifyAuthFailure(shape.description, shape.code));
      return;
    }

    const finish = async () => {
      if (cancelled || settled || answered.current) return;
      settled = true;
      answered.current = true;
      // Stop the clock BEFORE awaiting: provisioning is best-effort and can outlast the
      // timeout on a cold backend. Leaving the timer armed here is what used to show
      // "sign-in didn't complete" to a user who was, in fact, already signed in.
      if (timer) clearTimeout(timer);
      try {
        // Provisions the user (app_users upsert) + claims the pre-auth aeo_sid session.
        await api.me();
      } catch {
        /* provisioning is best-effort — the session is already valid without it */
      }
      if (!cancelled) router.replace(next);
    };

    // ── the email confirm link ──────────────────────────────────────────────────
    // Handled FIRST and on its own: verifyOtp is a definitive answer, so this path never
    // waits on a timeout and never has to guess.
    if (shape.kind === "token_hash") {
      void (async () => {
        const { data, error } = await client.auth.verifyOtp({
          token_hash: shape.tokenHash,
          type: shape.type,
        });
        if (cancelled) return;
        if (error) {
          // eslint-disable-next-line no-console -- the only breadcrumb a user can send back
          console.error("[auth/callback] verifyOtp failed", error.message);
          setEmail(null);
          fail(classifyAuthFailure(error.message, (error as { code?: string }).code ?? null));
          return;
        }
        setEmail(data.user?.email ?? null);
        void finish();
      })();
      return () => {
        cancelled = true;
      };
    }

    // ── the PKCE (`?code=`) and legacy implicit (`#access_token=`) paths ─────────
    // We deliberately do NOT call exchangeCodeForSession: detectSessionInUrl (lib/supabase.ts)
    // already owns the exchange, and a second attempt would consume an already-spent code and
    // fail. That decision is unchanged — it is the working Google leg.
    //
    // The exchange's own error is genuinely unobservable in @supabase/supabase-js 2.110.8:
    // GoTrueClient._initialize() catches it, returns { error } internally, and nothing public
    // exposes it — getSession() answers { session: null, error: null } either way, and
    // onAuthStateChange carries no error argument. So rather than invent an API, we use the
    // signals that DO exist: the error params above, getSession()'s own error, and the
    // INITIAL_SESSION event, which fires only AFTER initialization has run. A null session on
    // INITIAL_SESSION while a `?code=` was present means the exchange has definitively been
    // attempted and failed — which converts a blind 15-second wait into an immediate answer.
    void client.auth.getSession().then(({ data, error }) => {
      if (cancelled) return;
      if (data.session) return void finish();
      if (error) {
        // eslint-disable-next-line no-console -- the only breadcrumb a user can send back
        console.error("[auth/callback] getSession error", error.message);
        fail(classifyAuthFailure(error.message));
      }
    });

    const { data: sub } = client.auth.onAuthStateChange((event, session) => {
      if (cancelled) return;
      if (session) return void finish();
      if (event === "INITIAL_SESSION" && shape.kind === "code") {
        // eslint-disable-next-line no-console -- the only breadcrumb a user can send back
        console.error(
          "[auth/callback] a PKCE code was present but initialization produced no session. " +
            "The code was already used, or the code_verifier is missing from this browser's " +
            "storage (different browser, cleared storage, or a redirect through another origin).",
        );
        // Was `classifyAuthFailure("The sign-in link has expired or was already used.")` —
        // the same hardcoded string the 15s timeout below also passed, so two different
        // failures rendered byte-identical copy and the real reason reached console only.
        // This case is specifically "no code_verifier in THIS browser", for which resending
        // a link changes nothing; the copy has to say where to open it instead.
        fail(verifierMissingFailure());
      }
    });

    timer = setTimeout(() => {
      // Last resort, and now genuinely rare — INITIAL_SESSION normally answers within ms.
      // eslint-disable-next-line no-console -- the only breadcrumb a user can send back
      console.error(
        shape.kind === "none"
          ? "[auth/callback] no session, no ?code= and no #access_token — the provider " +
              "redirect carried nothing. Check the Supabase Redirect URLs allowlist for this origin."
          : "[auth/callback] the exchange never resolved within 15s.",
      );
      fail(
        shape.kind === "none"
          ? {
              kind: "unknown",
              title: "That sign-in didn't come back with a session",
              message:
                "The link didn't carry any sign-in information. Please start again from the " +
                "sign-in button.",
              canResend: false,
            }
          // Not "your link expired" — we do not know that. Nothing resolved, which is a
          // different (and much rarer, now INITIAL_SESSION answers in ms) condition, and
          // asserting a cause we have not established is what made this indistinguishable
          // from the case above.
          : timeoutFailure(),
      );
    }, PKCE_TIMEOUT_MS);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      sub.subscription.unsubscribe();
    };
  }, [next, params, router]);

  // Tick the resend cooldown down to zero.
  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setInterval(
      () => setCooldown(resendCooldownRemaining(lastSentAt.current, Date.now())),
      1000,
    );
    return () => clearInterval(id);
  }, [cooldown]);

  const resend = useCallback(async () => {
    if (!supabase || !email || cooldown > 0) return;
    setResendState("sending");
    const { error } = await supabase.auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}` },
    });
    if (error) {
      setResendState("failed");
      setFailure(classifyAuthFailure(error.message));
      return;
    }
    lastSentAt.current = Date.now();
    setCooldown(resendCooldownRemaining(lastSentAt.current, Date.now()));
    setResendState("sent");
  }, [cooldown, email, next]);

  return (
    <main className="flex min-h-[60vh] items-center justify-center p-6">
      <div className="card max-w-[420px] p-6 text-center">
        {failure ? (
          <>
            <h1 className="mb-2 text-[19px] font-semibold text-ink">{failure.title}</h1>
            <p className="mb-4 text-[13.5px] leading-[1.5] text-ink-500">{failure.message}</p>
            {failure.canResend && email && (
              <div className="mb-3">
                <button
                  type="button"
                  onClick={() => void resend()}
                  disabled={cooldown > 0 || resendState === "sending"}
                  className="btn-primary w-full justify-center disabled:opacity-60"
                >
                  {resendState === "sending"
                    ? "Sending…"
                    : cooldown > 0
                      ? `Send a new link (${cooldown}s)`
                      : "Send me a new link"}
                </button>
                {resendState === "sent" && (
                  <p className="mt-2 text-[13px] text-accent" role="status">
                    Sent — check your inbox for a fresh link.
                  </p>
                )}
              </div>
            )}
            <button type="button" onClick={() => router.replace(next)} className="btn-ghost justify-center">
              Go back
            </button>
          </>
        ) : (
          <p className="text-[13.5px] text-ink-500" role="status">
            Signing you in…
          </p>
        )}
      </div>
    </main>
  );
}

export default function AuthCallbackPage() {
  // useSearchParams needs a Suspense boundary to keep the route statically renderable.
  return (
    <Suspense fallback={null}>
      <CallbackInner />
    </Suspense>
  );
}
