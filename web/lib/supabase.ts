// v5 CH-07 — the browser Supabase client, DEGRADABLE. When NEXT_PUBLIC_SUPABASE_URL /
// NEXT_PUBLIC_SUPABASE_ANON_KEY are unset, `authEnabled` is false and everything stays
// anonymous (the marketing/free experience is byte-identical to before). The anon key is
// public by design; no secret ever ships in the bundle (the proxy's API_KEY stays
// server-only). The Bearer token is cached from onAuthStateChange so the SYNCHRONOUS
// api.ts headers() can attach it without every call site becoming async.

import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const authEnabled = Boolean(URL && ANON);

// Never construct during SSR — the session lives in the browser only.
// `detectSessionInUrl` must be ON for OAuth: Google sends the user back to /auth/callback
// carrying the PKCE `?code=`, and this client is what exchanges it for a session. `pkce`
// keeps the token out of the URL fragment (nothing sensitive lands in history or a Referer).
export const supabase: SupabaseClient | null =
  authEnabled && typeof window !== "undefined"
    ? createClient(URL!, ANON!, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
          flowType: "pkce",
        },
      })
    : null;

// The current access token, fed by onAuthStateChange (INITIAL_SESSION / SIGNED_IN /
// TOKEN_REFRESHED / SIGNED_OUT). Read synchronously by api.ts headers().
let cachedAccessToken: string | null = null;

if (supabase) {
  supabase.auth.onAuthStateChange((_event, session) => {
    cachedAccessToken = session?.access_token ?? null;
  });
}

export function getAccessToken(): string | null {
  return cachedAccessToken;
}

/** Force-read the freshest token (refreshing if needed) — used right after a login so the
 *  claim/gated call carries the Bearer even before onAuthStateChange has fired. */
export async function currentAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  cachedAccessToken = data.session?.access_token ?? null;
  return cachedAccessToken;
}

/** Start the Google OAuth handshake (v5 CH-07). Redirects the whole tab to Google, which
 *  returns to /auth/callback?next=<where the user was>; that route finishes the exchange
 *  and sends them back. `next` is captured here rather than read from the referrer so the
 *  round trip never loses the page the user was gated on. Resolves only on FAILURE — on
 *  success the tab has already navigated away. */
export async function signInWithGoogle(next?: string): Promise<{ error: string | null }> {
  if (!supabase) return { error: "Sign-in is not configured." };
  const target = next ?? `${window.location.pathname}${window.location.search}`;
  const redirectTo = `${window.location.origin}/auth/callback?next=${encodeURIComponent(target)}`;
  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo,
      // Ask Google for a refresh token + let the user pick an account rather than silently
      // reusing the one already signed in on the device.
      queryParams: { access_type: "offline", prompt: "select_account" },
    },
  });
  return { error: error ? error.message : null };
}

export type { Session };
