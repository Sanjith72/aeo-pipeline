"use client";

// v5 CH-07 — the OAuth return leg. Google → Supabase → here, carrying the PKCE `?code=`.
// The browser client (lib/supabase.ts, detectSessionInUrl) does the exchange as soon as it
// loads; this page only waits for the resulting session, provisions the user server-side
// (api.me() upserts app_users + claims the pre-auth aeo_sid session), and returns the user
// to exactly where they were gated. A route, not a modal, because OAuth is a full-page
// redirect — there is no live React state left to preserve by the time we land here.

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { authEnabled, supabase } from "@/lib/supabase";

// Only ever bounce to a path on this origin — an attacker-supplied URL in `next` would turn
// the login flow into an open redirect, and it fires on an already-authenticated victim.
//
// Validate by RESOLUTION, not by prefix. A startsWith("/") + !startsWith("//") check looks
// right but is bypassable: the browser resolves "/\evil.com", "/\tevil.com", "/\nevil.com"
// and "/\r/evil.com" all to https://evil.com/ — backslashes and raw control characters are
// normalised to path separators. Resolving against our own origin and comparing origins is
// the only check that cannot be out-cleverer'd.
function safeNext(raw: string | null): string {
  if (!raw || typeof window === "undefined") return "/";
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin !== window.location.origin) return "/";
    return `${u.pathname}${u.search}${u.hash}`;
  } catch {
    return "/";
  }
}

function CallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const next = safeNext(params.get("next"));

  useEffect(() => {
    let cancelled = false;

    // Supabase reports a refused/aborted consent as query params, not an exception.
    const denied = params.get("error_description") ?? params.get("error");
    if (denied) {
      setError(denied);
      return;
    }
    if (!authEnabled || !supabase) {
      router.replace(next);
      return;
    }

    const finish = async () => {
      try {
        await api.me();
      } catch {
        /* provisioning is best-effort — the session is already valid without it */
      }
      if (!cancelled) router.replace(next);
    };

    // The exchange may already be done (detectSessionInUrl runs on client construction) or
    // still in flight — cover both: check once, and listen for the SIGNED_IN that follows.
    void supabase.auth.getSession().then(({ data }) => {
      if (data.session && !cancelled) void finish();
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      if (session && !cancelled) void finish();
    });

    // Don't hang forever on a code that never exchanges (expired/replayed link).
    const timer = setTimeout(() => {
      if (!cancelled) setError("That sign-in link expired. Please try again.");
    }, 15000);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      sub.subscription.unsubscribe();
    };
  }, [next, params, router]);

  return (
    <main className="flex min-h-[60vh] items-center justify-center p-6">
      <div className="card max-w-[420px] p-6 text-center">
        {error ? (
          <>
            <h1 className="mb-2 text-[19px] font-semibold text-ink">Sign-in didn&apos;t complete</h1>
            <p className="mb-4 text-[13.5px] leading-[1.5] text-ink-500">{error}</p>
            <button type="button" onClick={() => router.replace(next)} className="btn-primary justify-center">
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
